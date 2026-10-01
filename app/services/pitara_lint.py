"""The rules a notification line must pass before it can reach anyone.

One set of rules for everything that can end up in the pitara: the shipped
files (tools/lint_pitara.py) and lines the model drafts (services/pitara_writer.py).
It checks the JSON and the claims, not the humour — that's for a human.
No settings, no database: importable from a script.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

# Stand-in lengths for placeholders, so the length check reflects a real push.
SAMPLE = {"name": 30, "price": 7, "mrp": 7, "save": 7, "discount": 2, "store": 8, "brand": 10,
          "category": 14, "day": 9,
          # context placeholders — filled by the sender of that kind of notification
          "target": 7, "value": 12, "count": 4, "query": 14, "coupon": 8, "sale": 20, "when": 15}
CONTEXT = {"target", "value", "count", "query", "coupon", "sale", "when"}
TITLE_MAX, BODY_MAX = 62, 130
NEEDS = {"price", "mrp", "discount", "brand", "store", "endsSoon", "lowest"}
KINDS = {"hot_deal", "crazy_deal", "nudge", "follow", "digest", "weekly_pick", "price_drop", "watchlist"}
TONES = {"tease", "dost", "self", "heart"}
TIMES = {"morning", "afternoon", "evening", "night"}
NEED_OF = {"price": ["price"], "mrp": ["mrp"], "save": ["mrp", "price"], "discount": ["discount"],
           "brand": ["brand"], "store": ["store"]}

# Fake urgency / scarcity (dark patterns) — only allowed when tied to real data
# (endsSoon), never as free-floating copy.
URGENCY = ["selling fast", "limited stock", "few left", "last chance", "hurry",
           "before it's gone", "before its gone", "going fast", "almost sold out", "act now",
           "tap before", "won't last", "wont last", "har koi le raha", "sab le rahe", "jaldi karo",
           "stock khatam", "sirf kuch bache"]
# Things we never joke about, and real brands/celebs/trademarks we never name.
BANNED = ["mota", "motapa", "moti", "kaala", "kali", "gora", "fat", "ugly", "bhikari", "garib",
          "hindu", "muslim", "christian", "sikh", "dalit", "brahmin", "pakistan", "modi", "rahul",
          "depressed", "depression", "suicide", "anxiety", "cancer", "breakup", "divorce", "single",
          "ipl", "bcci", "virat", "dhoni", "rohit", "kohli", "srk", "salman", "alia", "deepika",
          "amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa", "zara", "h&m", "nike", "adidas",
          "puma", "samsung", "apple", "iphone", "google", "netflix", "swiggy", "zomato"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")


def expand(text: str) -> int:
    return len(PLACEHOLDER.sub(lambda m: "x" * SAMPLE.get(m.group(1), 10), text))


def text_key(t: Dict[str, Any]) -> str:
    """Title + body with everything but letters and digits stripped — for spotting repeats."""
    return re.sub(r"\W+", "", f" {t.get('title', '')} {t.get('body', '')} ".lower())


def title_key(t: Dict[str, Any]) -> str:
    return re.sub(r"\W+", "", str(t.get("title", "")).lower())


def needs_for(title: str, body: str) -> List[str]:
    """The needs a line's placeholders imply (so they can't drift from the text)."""
    out: List[str] = []
    for p in PLACEHOLDER.findall(f"{title} {body}"):
        for need in NEED_OF.get(p, []):
            if need not in out:
                out.append(need)
    return out


def check(t: Dict[str, Any]) -> List[str]:
    """Problems with one line, on its own (duplicates are the caller's concern)."""
    problems: List[str] = []
    title, body = t.get("title", ""), t.get("body", "")
    if not isinstance(title, str) or not isinstance(body, str) or not title or not body:
        return ["missing title or body"]
    needs = set(t.get("needs", []))
    used = set(PLACEHOLDER.findall(title + " " + body))
    for p in used - set(SAMPLE):
        problems.append(f"unknown placeholder {{{p}}}")
    for p in used & {"price", "mrp", "discount", "brand", "store"}:
        if p not in needs:
            problems.append(f"uses {{{p}}} but 'needs' doesn't list it")
    if "save" in used and not {"mrp", "price"} <= needs:
        problems.append("uses {save} but 'needs' lacks mrp and price")
    if used & CONTEXT and not t.get("kinds"):
        problems.append(f"uses context placeholder {sorted(used & CONTEXT)} but has no 'kinds'")
    for n in needs:
        if n not in NEEDS:
            problems.append(f"unknown need {n!r}")
    for k in t.get("kinds", []):
        if k not in KINDS:
            problems.append(f"unknown kind {k!r}")
    if t.get("tone") not in TONES:
        problems.append(f"tone must be one of {sorted(TONES)}")
    md = t.get("min_discount")
    if md is not None and (not isinstance(md, int) or not 1 <= md <= 95 or "discount" not in needs):
        problems.append("min_discount must be 1-95 and needs must include discount")
    for tm in t.get("time", []):
        if tm not in TIMES:
            problems.append(f"unknown time {tm!r}")
    if expand(title) > TITLE_MAX:
        problems.append(f"title too long ({expand(title)} > {TITLE_MAX})")
    if expand(body) > BODY_MAX:
        problems.append(f"body too long ({expand(body)} > {BODY_MAX})")
    low = f" {title} {body} ".lower()
    for w in URGENCY:
        if w in low and "endsSoon" not in needs:
            problems.append(f"fake-urgency phrase {w!r} (only allowed with needs: endsSoon)")
    for w in BANNED:
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low):
            problems.append(f"banned word {w!r}")
    return problems


def check_drafted(t: Dict[str, Any]) -> List[str]:
    """Extra rules for lines a model wrote. The shipped lines were read by a person; these weren't,
    so anything that could state a fact the code doesn't control is refused outright."""
    problems = check(t)
    title, body = t.get("title", ""), t.get("body", "")
    if not isinstance(title, str) or not isinstance(body, str):
        return problems
    # "{discount}% off" is the one place a % belongs: the number is the placeholder's, the sign is ours.
    bare = PLACEHOLDER.sub("", re.sub(r"\{discount\}\s*%", "{discount}", f"{title} {body}"))
    if re.search(r"\d", bare) or "₹" in bare or "%" in bare or re.search(r"\brs\.?\b", bare, re.IGNORECASE):
        problems.append("a number, price or percentage written into the text (those come from placeholders)")
    if re.search(r"https?:|www\.|@\w|#\w", bare):
        problems.append("a link, mention or hashtag")
    if DEVANAGARI.search(bare):
        problems.append("not in roman letters")
    if len(EMOJI.findall(f"{title} {body}")) > 2:
        problems.append("too many emoji")
    if "{name}" not in title + body:
        problems.append("doesn't name the product ({name})")
    if t.get("needs") and "lowest" in t["needs"]:
        problems.append("a drafted line may not claim the lowest price")
    for fact in ("lowest", "sabse kam", "sabse sasta", "record low", "all-time", "all time", "guarantee", "free delivery",
                 "free shipping", "limited", "stock", "expire", "ends", "khatam"):
        if fact in bare.lower():
            problems.append(f"a claim the code can't back ({fact!r})")
    return problems


def lint(paths: List[Path]) -> List[str]:
    """Problems across the shipped files, including duplicates between them."""
    errors: List[str] = []
    seen_ids: Dict[str, str] = {}
    seen_text: Dict[str, str] = {}
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read {path}: {exc}")
            continue
        for i, t in enumerate(doc.get("templates", [])):
            tid = t.get("id") or f"{path.name}#{i}"
            if tid in seen_ids:
                errors.append(f"{tid}: duplicate id (also in {seen_ids[tid]})")
            seen_ids[tid] = path.name
            for problem in check(t):
                errors.append(f"{tid}: {problem}")
            key = text_key(t)
            if key in seen_text:
                errors.append(f"{tid}: same text as {seen_text[key]}")
            seen_text[key] = tid
    return errors
