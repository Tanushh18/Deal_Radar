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
        "categories": ["Women Fashion", "Beauty"],
        "keywords": ["women", "womens", "ladies", "girls", "kurti", "saree", "lehenga", "anarkali", "dupatta",
                     "salwar", "leggings", "lingerie", "handbag", "sling bag", "clutch", "heels", "lipstick",
                     "makeup", "kajal", "eyeliner", "jewellery", "jewelry", "earring", "necklace", "bangles",
                     "maxi dress", "crop top"],
        "stores": [],
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


def _clean(rule: Dict[str, Any]) -> Dict[str, Any]:
    cats = [c for c in rule.get("categories") or [] if c in taxonomy.CATEGORIES]
    words = []
    for w in rule.get("keywords") or []:
        w = str(w).strip().lower()
        if _SAFE_WORD.match(w) and w not in words:
            words.append(w)
    stores = []
    for s in rule.get("stores") or []:
        s = str(s).strip().lower()
        if _SAFE_WORD.match(s) and s not in stores:
            stores.append(s)
    return {"preset": str(rule.get("preset") or "custom"), "label": str(rule.get("label") or "Custom")[:40],
            "categories": cats, "keywords": words[:60], "stores": stores[:20]}


def get() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        raw = db.get_meta(META_KEY)
        if raw:
            try:
                _cache = _clean(json.loads(raw))
            except (ValueError, TypeError):
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
    return "(" + " OR ".join(parts) + ")" if parts else "(1 = 0)"


def lead_categories() -> List[str]:
    return list(get()["categories"])
