"""AI enrichment for newly-ingested deals — Groq (openai/gpt-oss-120b), one key.

Design constraints, all enforced here rather than assumed:
  * Never called from a live user request — only from the ingest cycle, on
    brand-new deals. Call volume is bounded by how many new deals arrive,
    never by site traffic.
  * Most deals are handled by the existing rule-based copy in devices.py/
    push.py and never reach this module at all — should_enrich() is a cheap
    gate that only lets through the minority that actually need a model
    call (see its docstring). This is what keeps a single free-tier key
    sufficient.
  * Every cap (RPM, daily requests, daily tokens, per-cycle count) is
    enforced server-side against a DB-backed counter, buffered well under
    Groq's real ceiling so counting drift or a restart mid-day can't trip it.
  * Best-effort only: any failure (quota exhausted, timeout, bad response,
    no key configured) is swallowed and the deal just keeps its existing
    rule-based copy. This module must never raise into the ingest loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.ai_enrich")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
KEY_LABEL = "primary"  # single key for now; a second would just add a label
REQUEST_TIMEOUT = 8.0
MAX_RESPONSE_TOKENS = 120

_last_call_at = 0.0
_lock = asyncio.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_row() -> Dict[str, int]:
    row = db.query_one(
        "SELECT requests, tokens FROM ai_usage WHERE day = ? AND key_label = ?",
        (_today(), KEY_LABEL),
    )
    return {"requests": row["requests"], "tokens": row["tokens"]} if row else {"requests": 0, "tokens": 0}


def _record_usage(tokens_used: int) -> None:
    db.execute(
        "INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(day, key_label) DO UPDATE SET "
        "requests = requests + 1, tokens = tokens + excluded.tokens",
        (_today(), KEY_LABEL, tokens_used),
    )


def _budget_ok() -> bool:
    usage = _usage_row()
    return (
        usage["requests"] < settings.groq_max_requests_per_day
        and usage["tokens"] < settings.groq_max_tokens_per_day
    )


# --------------------------------------------------------------- gate

def should_enrich(deal: Dict[str, Any]) -> bool:
    """The minority of deals worth spending a model call on.

    Most deals get the existing templated copy (price/discount are already
    clear on their own) and never reach here. A deal qualifies only if the
    rules genuinely couldn't handle it:
      * category or brand missing — the regex parser gave up, and browsing/
        follows depend on these being set.
      * flagged suspicious_mrp — worth a one-line reason, not just a badge.
      * a standout deal (high discount + real price) — the one case where a
        sharper hook than the templated line is worth writing.
    """
    if not settings.ai_enrich_enabled:
        return False
    if not deal.get("category") or not deal.get("brand"):
        return True
    if "suspicious_mrp" in (deal.get("flags") or []):
        return True
    discount = deal.get("discount_pct") or 0
    price = deal.get("price") or 0
    if discount >= 50 and price >= 300:
        return True
    return False


# --------------------------------------------------------------- the call

_SYSTEM_PROMPT = (
    "You tag Indian e-commerce deals for a deals app. Reply with ONLY a compact JSON "
    "object, no prose, no markdown fences. Keys (all optional, omit what you don't know): "
    '"hook" (<=12 words, punchy one-line reason to click, no emoji, no price repeated), '
    '"category" (one of: Women Fashion, Men Fashion, Electronics, Appliances, Home & Kitchen, '
    '"Grocery, Beauty, Footwear, Toys, Other), '
    '"brand" (short brand name if identifiable), '
    '"mrp_reason" (<=15 words, only if asked to judge a suspicious MRP).'
)


def _build_prompt(deal: Dict[str, Any]) -> str:
    parts = [f"Title: {(deal.get('title') or '')[:200]}"]
    if deal.get("price"):
        parts.append(f"Price: ₹{deal['price']}")
    if deal.get("mrp"):
        parts.append(f"MRP: ₹{deal['mrp']}")
    if deal.get("discount_pct"):
        parts.append(f"Discount: {deal['discount_pct']}%")
    if deal.get("store"):
        parts.append(f"Store: {deal['store']}")
    need = []
    if not deal.get("category"):
        need.append("category")
    if not deal.get("brand"):
        need.append("brand")
    if "suspicious_mrp" in (deal.get("flags") or []):
        need.append("mrp_reason (the MRP looks inflated versus this product's usual price)")
    if need:
        parts.append("Missing/needed: " + ", ".join(need))
    parts.append("Also give a punchy one-line hook.")
    return "\n".join(parts)


async def _call_groq(deal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    global _last_call_at

    if not settings.ai_enrich_enabled:
        return None

    async with _lock:
        if not _budget_ok():
            return None
        # RPM guard: enforce a floor spacing between calls (60 / max_rpm seconds).
        min_gap = 60.0 / max(1, settings.groq_max_requests_per_minute)
        wait = _last_call_at + min_gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.groq_model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": _build_prompt(deal)},
                        ],
                        "max_tokens": MAX_RESPONSE_TOKENS,
                        "temperature": 0.4,
                    },
                )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            log.info("Groq call failed (network): %s", exc)
            return None

        if resp.status_code == 429:
            log.info("Groq rate-limited; skipping enrichment for the rest of this cycle")
            # Burn the remaining daily budget so should_enrich's caller stops
            # retrying against a key that's clearly already throttled today.
            db.execute(
                "INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(day, key_label) DO UPDATE SET requests = excluded.requests",
                (_today(), KEY_LABEL, settings.groq_max_requests_per_day, 0),
            )
            return None
        if resp.status_code != 200:
            log.info("Groq call failed: HTTP %s", resp.status_code)
            return None

        try:
            body = resp.json()
            usage_tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError):
            return None

        _record_usage(usage_tokens)

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # Model occasionally wraps in fences despite instructions.
        stripped = content.strip().strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            log.info("Groq response wasn't valid JSON, dropping")
            return None
    return parsed if isinstance(parsed, dict) else None


def _apply(deal_id: str, result: Dict[str, Any]) -> None:
    sets, params = [], []
    hook = str(result.get("hook") or "").strip()[:140]
    if hook:
        sets.append("ai_hook = ?")
        params.append(hook)
    category = str(result.get("category") or "").strip()
    if category:
        sets.append("category = COALESCE(NULLIF(category, ''), ?)")
        params.append(category)
    brand = str(result.get("brand") or "").strip()[:60]
    if brand:
        sets.append("brand = COALESCE(NULLIF(brand, ''), ?)")
        params.append(brand)
    reason = str(result.get("mrp_reason") or "").strip()[:160]
    if reason:
        sets.append("ai_mrp_reason = ?")
        params.append(reason)
    if not sets:
        return
    sets.append("dirty = 1")
    params.append(deal_id)
    db.execute(f"UPDATE deals SET {', '.join(sets)} WHERE id = ?", params)


# --------------------------------------------------------------- entrypoint

async def enrich_new_deals(deal_ids: List[str]) -> int:
    """Best-effort enrichment for this cycle's new deals. Never raises."""
    if not settings.ai_enrich_enabled or not deal_ids:
        return 0
    done = 0
    for deal_id in deal_ids[: settings.groq_max_calls_per_cycle]:
        if not _budget_ok():
            break
        try:
            row = db.query_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
            if not row:
                continue
            deal = dict(row)
            deal["flags"] = json.loads(deal.get("flags") or "[]") if isinstance(deal.get("flags"), str) else (deal.get("flags") or [])
            if not should_enrich(deal):
                continue
            result = await _call_groq(deal)
            if result:
                _apply(deal_id, result)
                done += 1
        except Exception as exc:  # noqa: BLE001 — enrichment must never break ingestion
            log.warning("Enrichment failed for deal %s: %s", deal_id, exc)
    return done
