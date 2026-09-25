"""What leads browsing ("show on top"), set from the admin panel.

A rule is categories + keywords + stores; any deal matching one of them ranks
first in browse orders (Newest / Top rated / Best match / trending) and gets
a nudge in search. Explicit sorts (price, discount, ending) are never touched.
Stored in meta and mirrored to the Sheet's Settings tab so it survives restarts;
PRIORITY_AUDIENCE picks the starting preset.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .. import db
from ..config import settings
from . import taxonomy

META_KEY = "priority_rule"

PRESETS: Dict[str, Dict[str, Any]] = {
    "women": {
        "label": "Women",
        "categories": ["Women Fashion", "Women Accessories", "Beauty"],
        "keywords": ["women", "womens", "woman", "ladies", "girls", "kurti", "kurta", "saree", "lehenga",
                     "anarkali", "dupatta", "salwar", "leggings", "palazzo", "lingerie", "bra", "nightwear",
                     "nighty", "handbag", "sling bag", "tote", "clutch", "heels", "bellies", "lipstick",
                     "makeup", "kajal", "eyeliner", "mascara", "nail polish", "jewellery", "jewelry",
                     "earring", "necklace", "bangles", "anklet", "mangalsutra", "maxi dress", "crop top",
                     "gown", "blouse", "scrunchie", "hair clip"],
        "stores": [],
        # Beauty / fashion that is plainly for men doesn't lead a women's feed —
        # unless the post also names women ("men & women" is unisex, kept).
        "exclude_keywords": ["men", "mens", "man", "male", "gents", "boys", "him", "beard", "trimmer",
                             "shaver", "shaving", "razor", "axe", "beardo", "gillette", "ustraa"],
        "unless_keywords": ["women", "womens", "woman", "ladies", "lady", "girls", "girl", "her",
                            "female", "unisex"],
    },
    "men": {
        "label": "Men",
        "categories": ["Men Fashion"],
        "keywords": ["men", "mens", "boys", "shirt", "trouser", "beard", "trimmer", "sneakers"],
        "stores": [],
    },
    "electronics": {
        "label": "Electronics",
        "categories": ["Electronics", "Appliances"],
        "keywords": ["headphone", "earbuds", "smartwatch", "phone", "laptop", "power bank", "charger"],
        "stores": [],
    },
    "home": {
        "label": "Home & Kitchen",
        "categories": ["Home & Kitchen", "Grocery"],
        "keywords": ["kitchen", "cookware", "bedsheet", "storage", "mixer", "kettle"],
        "stores": [],
    },
    "off": {"label": "Nothing (neutral)", "categories": [], "keywords": [], "stores": []},
}

_SAFE_WORD = re.compile(r"^[a-z0-9 &'.+-]{2,40}$")
_cache: Optional[Dict[str, Any]] = None


def _words(values: Any, limit: int) -> List[str]:
    words: List[str] = []
    for w in values or []:
        w = str(w).strip().lower()
        if _SAFE_WORD.match(w) and w not in words:
            words.append(w)
    return words[:limit]


def _clean(rule: Dict[str, Any]) -> Dict[str, Any]:
    cats = [c for c in rule.get("categories") or [] if c in taxonomy.CATEGORIES]
    preset = PRESETS.get(str(rule.get("preset") or ""), {})
    # The admin form only edits categories/keywords/stores; the exclusion
    # lists come from the preset unless a rule carries its own.
    exclude = rule["exclude_keywords"] if "exclude_keywords" in rule else preset.get("exclude_keywords", [])
    unless = rule["unless_keywords"] if "unless_keywords" in rule else preset.get("unless_keywords", [])
    return {"preset": str(rule.get("preset") or "custom"), "label": str(rule.get("label") or "Custom")[:40],
            "categories": cats, "keywords": _words(rule.get("keywords"), 60),
            "stores": _words(rule.get("stores"), 20),
            "exclude_keywords": _words(exclude, 40), "unless_keywords": _words(unless, 20)}


def get() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        raw = db.get_meta(META_KEY)
        if raw:
            try:
                stored = json.loads(raw)
                if stored.get("preset") in PRESETS and "exclude_keywords" not in stored:
                    # Saved before presets carried exclusions: pick up the
                    # current preset (its keyword list has grown too).
                    stored = {**PRESETS[stored["preset"]], "preset": stored["preset"]}
                _cache = _clean(stored)
            except (ValueError, TypeError, AttributeError):
                _cache = None
        if _cache is None:
            name = settings.priority_audience if settings.priority_audience in PRESETS else "off"
            _cache = _clean({**PRESETS[name], "preset": name})
    return _cache


def save(rule: Dict[str, Any]) -> Dict[str, Any]:
    global _cache
    if rule.get("preset") in PRESETS and not any(rule.get(k) for k in ("categories", "keywords", "stores")):
        rule = {**PRESETS[rule["preset"]], "preset": rule["preset"]}
    cleaned = _clean(rule)
    db.set_meta(META_KEY, json.dumps(cleaned))
    _cache = cleaned
    return cleaned


def reset_cache() -> None:
    global _cache
    _cache = None


def sql() -> str:
    """SQL boolean (inlined in ORDER BY) — inputs are whitelisted/regex-checked in _clean."""
    rule = get()
    parts: List[str] = []
    if rule["categories"]:
        parts.append("category IN (" + ", ".join("'" + c.replace("'", "''") + "'" for c in rule["categories"]) + ")")
    # Word-start match on space-padded text, so "men" hits "men's"/"mens" but never "women".
    padded = ("(' ' || REPLACE(REPLACE(REPLACE(REPLACE(LOWER(COALESCE(search_blob, title, '')), "
              "'(', ' '), '-', ' '), '/', ' '), ',', ' ') || ' ')")
    for w in rule["keywords"]:
        parts.append(f"{padded} LIKE '% " + w.replace("'", "''") + "%'")
    if rule["stores"]:
        parts.append("LOWER(store) IN (" + ", ".join("'" + s.replace("'", "''") + "'" for s in rule["stores"]) + ")")
    # Never a bare "0": in ORDER BY SQLite reads an integer literal as a column number.
    if not parts:
        return "(1 = 0)"
    match = "(" + " OR ".join(parts) + ")"
    if not rule["exclude_keywords"]:
        return match
    # Exclusions are whole words ("men" must not hit "menstrual"), on text with
    # all punctuation spaced out so "men's" reads as "men s".
    words = "(' ' || " + _spaced("LOWER(COALESCE(title, '') || ' ' || COALESCE(search_blob, ''))") + " || ' ')"

    def any_word(ws: List[str]) -> str:
        return "(" + " OR ".join(f"{words} LIKE '% " + w.replace("'", "''") + " %'" for w in ws) + ")"

    excluded = any_word(rule["exclude_keywords"])
    if rule["unless_keywords"]:
        excluded = f"({excluded} AND NOT {any_word(rule['unless_keywords'])})"
    return f"({match} AND NOT {excluded})"


def _spaced(expr: str) -> str:
    for ch in _WORD_CHARS:
        expr = f"REPLACE({expr}, '{ch.replace(chr(39), chr(39) * 2)}', ' ')"
    return expr


_KEYWORD_CHARS = "(-/,"
_WORD_CHARS = "()-/,.:;|&!'+"
_compiled: Dict[str, Dict[str, Any]] = {}   # rule json -> compiled patterns


def _spaced_text(text: str, chars: str) -> str:
    for ch in chars:
        text = text.replace(ch, " ")
    return f" {text} "


def _compile(rule: Dict[str, Any]) -> Dict[str, Any]:
    key = json.dumps(rule, sort_keys=True)
    if key not in _compiled:
        def words(ws: List[str], whole: bool) -> Optional["re.Pattern[str]"]:
            if not ws:
                return None
            tail = " " if whole else ""
            return re.compile("|".join(" " + re.escape(w) + tail for w in ws))
        if len(_compiled) > 8:  # only ever a couple of rules live at once
            _compiled.clear()
        _compiled[key] = dict(cats=set(rule["categories"]), stores=set(rule["stores"]),
                              keywords=words(rule["keywords"], False),
                              exclude=words(rule["exclude_keywords"], True),
                              unless=words(rule["unless_keywords"], True))
    return _compiled[key]


def matches(deal: Dict[str, Any], rule: Optional[Dict[str, Any]] = None) -> bool:
    """Python twin of sql() for rows already fetched — same rule, same result.

    Evaluating sql() inline costs a chain of REPLACE()s per keyword per row
    (~0.8s over 5,000 deals); doing it here over the light rows is ~100x
    cheaper, so search uses this and sql() stays for small ORDER BYs.
    `rule` defaults to the admin's "show on top" rule; pass another to test
    against it instead (hot pushes always favour the women preset).
    """
    c = _compile(rule or get())
    blob = (deal.get("search_blob") or deal.get("title") or "").lower()
    hit = (
        (deal.get("category") in c["cats"])
        or (c["keywords"] is not None and bool(c["keywords"].search(_spaced_text(blob, _KEYWORD_CHARS))))
        or ((deal.get("store") or "").lower() in c["stores"])
    )
    if not hit or c["exclude"] is None:
        return hit
    text = _spaced_text(f"{deal.get('title') or ''} {deal.get('search_blob') or ''}".lower(), _WORD_CHARS)
    if c["exclude"].search(text) and not (c["unless"] is not None and c["unless"].search(text)):
        return False
    return True


def is_women(deal: Dict[str, Any]) -> bool:
    """Is this a women's deal? Always the women preset, whatever the admin's rule."""
    return matches(deal, _clean({**PRESETS["women"], "preset": "women"}))


def lead_categories() -> List[str]:
    return list(get()["categories"])
