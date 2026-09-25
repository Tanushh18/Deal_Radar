"""The noise gate: which Telegram posts deserve a card.

Deal channels mix real deals with everything else. Measured on the live feed
(800 deals, Sept 2026) about 4 in 10 cards were not "one product at one price":

  * round-ups   — "Myntra : Upto 70% Off On Top Branded Shoes",
                  "AJIO : T-shirts Starts @80", "Jeans Under @799"
  * promos      — "Refer 6 Friends Get ₹25,000 Voucher", "Swiggy Dineout
                  Offer", "Biggest Pre Sale Is Going Live Tonight"
  * headers     — "Be Active Tonight!", "Buy Max", "Amazon Brand Clothing"
  * no price    — "AJIO : Beauty Products."

A post passes only if it names one buyable product, with a price, a link that
leads to a store, and a photo. Everything is judged on what the post itself
says; nothing here trusts the channel's own "loot"/"steal" hype.

Two entry points:
  * text_reason(deal) — cheap, text-only; run before any network work.
  * reject_reason(deal) — the full gate (adds link destination + photo);
    store.save_deal applies it to new cards only, so a messy repost of a
    product we already show still counts as corroboration.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .. import db
from . import links, parser, taxonomy

log = logging.getLogger(__name__)

# Bump to re-run the sweep over stored deals after changing the rules.
QUALITY_VERSION = 1
LOW_QUALITY = "low_quality"

# Anywhere in the post: things that are never a product deal.
_PROMO_TEXT_RE = re.compile(
    r"\brefer\s*(?:&|and|n)?\s*earn\b|\brefer\s+\d+\s+friends?\b|\breferral\s+(?:code|link|bonus)\b"
    r"|\blifetime\s+free\s+(?:credit\s+)?card\b|\bapply\s+(?:for\s+)?(?:a\s+)?credit\s+card\b"
    r"|\bdemat\b|\bpersonal\s+loan\b|\binstant\s+loan\b|\brummy\b|\bcasino\b|\bbetting\b|\bteen\s*patti\b"
    r"|\bcrypto\b|\bearn\s+(?:money|daily|₹|rs)|\bwork\s+from\s+home\b|\bfree\s+recharge\b"
    r"|\bsign\s*up\s+bonus\b|\bjoining\s+bonus\b",
    re.IGNORECASE,
)
# In the product line: offers that aren't a product at a price.
_PROMO_TITLE_RE = re.compile(
    r"\bvouchers?\b|\bcashback\b|\brecharge\b|\bdineout\b|\bgame\s+credits?\b|\bsubscriptions?\b"
    r"|\bpre[\s-]?sale\b|\bsale\s+(?:is\s+)?(?:live|starts?|begins?)\b|\blive\s+(?:today|now)\b"
    r"|\btonight\b|\b(?:at|from|till|until)\s+(?:12\s*)?midnight\b|\bgift\s*cards?\b|\bbill\s+pay|\bfree\s+trial\b|\bupi\b|\bpaytm\b"
    r"|\bphonepe\b|\bgpay\b|\b(?:credit|debit)\s+cards?\b|\bsupercoins?\s+(?:offer|deal)",
    re.IGNORECASE,
)
# In the product line: many products at a floor/range price.
_ROUNDUP_RE = re.compile(
    r"\bup\s*to\s*\d{1,2}\s*%|\bupto\s*\d{1,2}|\bmin(?:imum)?\.?\s*\d{1,2}\s*%|\bflat\s*\d{1,2}\s*%"
    r"|\bstart(?:s|ing)?\b(?:\s+(?:from|at|@))?|\bfrom\s*(?:just\s*|at\s*)?(?:₹|rs\.?|inr|@|\d)"
    r"|\bunder\s*(?:₹|rs\.?|@)?\s*\d|\b(?:sale|fest|festival|carnival|bonanza)\b"
    r"|\b(?:deals?|offers?)\s+on\b|\bbuy\s*\d+\s*get\s*\d+|\bbogo\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Words that say nothing about *which* product this is.
_GENERIC = {
    "apply", "coupon", "code", "buy", "max", "loot", "lut", "deal", "deals", "off", "price",
    "prices", "only", "now", "amazon", "flipkart", "myntra", "ajio", "meesho", "shopsy", "nykaa",
    "brand", "branded", "brands", "products", "product", "items", "item", "steal", "grab", "rush",
    "hour", "today", "offer", "offers", "sale", "fast", "hurry", "limited", "stock", "big", "mega",
    "super", "best", "lowest", "get", "free", "extra", "flat", "upto", "for", "and", "the", "with",
    "new", "top", "all", "just", "pack", "combo", "online", "shopping", "live", "hot", "clothing",
    "fashion", "wear", "collection", "range", "tonight", "essentials", "home", "loots", "tricks",
    "bank", "card", "cards", "discount", "save", "click", "link", "here", "via", "use", "worth",
}
MIN_PRODUCT_WORDS = 2
MAX_STORE_LINKS = 2   # a post with 3+ shop links is a list of products, not a deal


def _title(deal: Dict[str, Any]) -> str:
    return deal.get("title") or ""


def _text(deal: Dict[str, Any]) -> str:
    return deal.get("raw_text") or _title(deal)


def product_words(title: str) -> List[str]:
    """The words in a title that identify a product (brand, type, model)."""
    words = []
    for token in _TOKEN_RE.findall(parser.EMOJI_RE.sub(" ", title.lower())):
        if len(token) < 3 or token.isdigit() or token in _GENERIC:
            continue
        words.append(token)
    return words


def _shop_links(text: str) -> List[str]:
    urls = dict.fromkeys(u.rstrip(".,)") for u in parser.URL_RE.findall(text or ""))
    return [u for u in urls if not links.is_social(u)]


def text_reason(deal: Dict[str, Any]) -> Optional[str]:
    """Why the post itself isn't a single product deal, or None."""
    title, text = _title(deal), _text(deal)
    if _PROMO_TEXT_RE.search(text) or _PROMO_TITLE_RE.search(title):
        return "promo"
    # The deal's own url counts too: raw_text restored from Sheets is cut at
    # 800 chars, which can drop a link Telegram appended at the end.
    if not _shop_links(f"{text} {deal.get('url') or ''}"):
        return "no_link"
    if not deal.get("price"):
        return "no_price"
    if _ROUNDUP_RE.search(title):
        return "roundup"
    if len(product_words(title)) < MIN_PRODUCT_WORDS:
        return "vague"
    if len(_shop_links(text)) > MAX_STORE_LINKS:
        return "roundup"
    return None


def reject_reason(deal: Dict[str, Any]) -> Optional[str]:
    """The full gate for a new card: text, where the link leads, and a photo."""
    reason = text_reason(deal)
    if reason:
        return reason
    destination = deal.get("resolved_url") or deal.get("url") or ""
    if links.is_product_page(destination) is False:
        return "roundup"   # the link opens a search / brand / sale page
    if not deal.get("image_url"):
        return "no_image"
    return None


def _flags(value: Any) -> List[str]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def sweep_stored() -> Dict[str, int]:
    """Once per QUALITY_VERSION: apply the current rules to deals already stored.

    Without this the old noise stays — and the feed's "never run dry" fallback
    would keep resurfacing it from expired rows. Also fixes, in the same pass,
    two things older code got wrong: gender-swapped fashion categories and
    photo links that point at a card that was never saved (every one of those
    404s). Noise is marked dead + low_quality; a deal whose only fault is a
    missing photo is just expired. Nothing is deleted.
    """
    if db.get_meta("quality_version") == str(QUALITY_VERSION):
        return {"checked": 0, "removed": 0, "recategorised": 0, "images_cleared": 0}

    from . import store  # local: store imports this module

    stats = {"checked": 0, "removed": 0, "recategorised": 0, "images_cleared": 0}
    updates = []
    for row in db.query("SELECT * FROM deals WHERE status IN ('live', 'expired')"):
        deal = dict(row)
        deal["flags"] = _flags(deal.get("flags"))
        stats["checked"] += 1
        changed = False

        image = deal.get("image_url") or ""
        fixed_image = store.rebase_image_url(image, deal["id"]) if image else ""
        if fixed_image != image:
            deal["image_url"] = fixed_image
            stats["images_cleared"] += 1
            changed = True

        category, subcategory = taxonomy.classify(f"{deal.get('title') or ''} {(deal.get('raw_text') or '')[:400]}")
        if (category, subcategory) != (deal.get("category"), deal.get("subcategory")):
            deal["category"], deal["subcategory"] = category, subcategory
            deal["search_blob"] = parser.build_search_blob(deal)
            stats["recategorised"] += 1
            changed = True

        reason = reject_reason(deal)
        if reason == "no_image":
            # A good deal that only lacks a (working) photo isn't noise: take
            # it out of the feed, and let a repost with a photo bring it back.
            if deal["status"] == "live":
                deal["status"] = "expired"
                stats["removed"] += 1
                changed = True
        elif reason:
            deal["status"] = "dead"
            deal["flags"] = sorted(set(deal["flags"]) | {LOW_QUALITY, f"lq_{reason}"})
            stats["removed"] += 1
            changed = True

        if changed:
            updates.append((deal["image_url"], deal["category"], deal["subcategory"], deal["search_blob"],
                            deal["status"], json.dumps(deal["flags"]), deal["id"]))
    if updates:
        db.execute_many(
            "UPDATE deals SET image_url = ?, category = ?, subcategory = ?, search_blob = ?, "
            "status = ?, flags = ?, dirty = 1 WHERE id = ?",
            updates,
        )
    db.set_meta("quality_version", str(QUALITY_VERSION))
    log.info("Quality sweep: %s", stats)
    return stats
