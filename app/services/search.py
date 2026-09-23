"""Query engine over the cached deals.

Two-stage: SQL narrows by hard filters (price, store, the user's own channels)
and pulls a light projection of every candidate, then Python matches and ranks.
At a few thousand live deals this is far simpler than a search index and still
well under 100ms; only the page being returned is loaded in full.

Each query word is matched independently across title, brand, store, category
and subcategory, through taxonomy synonyms (so "kurta" finds "kurti"), and
through a typo-tolerant pass against the candidate vocabulary. Deals matching
more of the words rank higher, and the stored deal score is folded in so a
mediocre-but-relevant match ranks below a great one.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from rapidfuzz import process
from rapidfuzz.distance import OSA

from .. import db
from . import taxonomy

SORTS = {
    "relevance": None,
    "best": "score DESC",
    "newest": "posted_at DESC",
    "discount": "discount_pct DESC",
    "price_low": "price ASC",
    "price_high": "price DESC",
    "ending": "expires_at ASC",
}

_CANDIDATE_CAP = 5000
_LIGHT_COLUMNS = "id, title, brand, store, category, subcategory, search_blob, score"
_WORD_RE = re.compile(r"[a-z0-9&']+")
_STOPWORDS = {"the", "and", "for", "with", "of", "in", "on", "to", "a", "an", "under", "below", "at", "by"}

# Weight of the best way a single query word hit a deal.
_W_TITLE = 1.0
_W_BRAND = 1.0
_W_STORE = 0.9
_W_SUBCATEGORY = 0.85
_W_CATEGORY = 0.75
_W_BLOB = 0.75
_W_SYNONYM = 0.7
_W_FUZZY = 0.6
_FIELD_WEIGHTS = [
    ("title", _W_TITLE), ("brand", _W_BRAND), ("store", _W_STORE),
    ("subcategory", _W_SUBCATEGORY), ("category", _W_CATEGORY), ("blob", _W_BLOB),
]


def _words(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _hit(text: str, padded: str, term: str) -> float:
    if f" {term}" in padded:
        return 1.0
    # Short terms ("pb", "mi", "ac") only count at a word start — mid-word
    # they'd hit half the catalogue. Longer ones mid-word ("phone" inside
    # "headphone") still count, just weaker than a real word hit.
    if len(term) > 3 and term in text:
        return 0.5
    return 0.0


class _Plan:
    """A parsed query: its words, their synonyms, and typo corrections."""

    def __init__(self, raw: str, typing: bool = False):
        self.raw = " ".join(_words(raw))
        tokens = [w for w in _words(raw) if len(w) > 1 and w not in _STOPWORDS]
        if not tokens and self.raw:
            tokens = [w for w in _words(raw) if w]
        self.tokens: List[str] = list(dict.fromkeys(tokens))
        self.typing = typing
        self.synonyms: Dict[str, Set[str]] = {t: set() for t in self.tokens}
        self.fuzzy: Dict[str, Set[str]] = {t: set() for t in self.tokens}
        self._expand()

    def _expand(self) -> None:
        if not self.tokens:
            return
        padded = f" {' '.join(self.tokens)} "
        allowed = taxonomy.expand_query(self.raw)

        def attach(trigger: str, group: Iterable[str]) -> None:
            for tok in trigger.split():
                if tok in self.synonyms:
                    self.synonyms[tok].update(g.strip() for g in group if g.strip() != tok)

        # expand_query matches taxonomy terms as raw substrings of the query, so
        # "headphones" drags in the whole Mobile group via "phone". Only keep
        # groups whose trigger is a whole word/phrase of the query.
        for term, group in taxonomy.SYNONYM_GROUPS.items():
            trigger = term.strip()
            if trigger and f" {trigger} " in padded:
                attach(trigger, {g for g in group if g in allowed})
        for category, subs in taxonomy.CATEGORIES.items():
            for name, terms in [(category, [t for ts in subs.values() for t in ts])] + list(subs.items()):
                trigger = " ".join(_words(name))
                if trigger and f" {trigger} " in padded:
                    attach(trigger, {t.lower() for t in terms})

    def correct(self, vocab: List[str]) -> None:
        """Map misspelt words onto words that actually exist in the candidates."""
        if not vocab:
            return
        for i, tok in enumerate(self.tokens):
            if len(tok) < 5 or not tok.isalpha():
                continue
            if any(tok in w for w in vocab):
                continue
            max_edits = 2 if len(tok) >= 8 else 1
            for word, _, _ in process.extract(
                tok, vocab, scorer=OSA.distance, score_cutoff=max_edits, limit=8
            ):
                self.fuzzy[tok].add(word)
            is_last = i == len(self.tokens) - 1
            if self.typing and is_last:
                # Mid-keystroke "headphn" should already reach "headphones".
                for word in vocab:
                    if len(word) > len(tok) and OSA.distance(tok, word[: len(tok)]) <= 1:
                        self.fuzzy[tok].add(word)
            for word in list(self.fuzzy[tok]):
                self.synonyms[tok].update(
                    g.strip() for g in taxonomy.SYNONYM_GROUPS.get(word, ()) if g.strip() != tok
                )


def _token_weight(tok: str, fields: Dict[str, Tuple[str, str]], word_set: Set[str], plan: _Plan) -> float:
    best = 0.0
    for name, weight in _FIELD_WEIGHTS:
        text, padded = fields[name]
        if text:
            best = max(best, weight * _hit(text, padded, tok))
            if best == weight:
                return best
    blob, blob_p = fields["blob"]
    for syn in plan.synonyms.get(tok, ()):
        if f" {syn} " in blob_p or (len(syn) > 3 and f" {syn}" in blob_p):
            best = max(best, _W_SYNONYM)
            break
    if best < _W_FUZZY and plan.fuzzy.get(tok) and plan.fuzzy[tok] & word_set:
        best = _W_FUZZY
    return best


def _fields(deal: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    def norm(value: Any) -> Tuple[str, str]:
        text = " ".join(_words(str(value or "")))
        return text, f" {text} "

    blob_parts = [deal.get("search_blob"), deal.get("title"), deal.get("brand"),
                  deal.get("store"), deal.get("category"), deal.get("subcategory")]
    return {
        "title": norm(deal.get("title")),
        "brand": norm(deal.get("brand")),
        "store": norm(deal.get("store")),
        "subcategory": norm(deal.get("subcategory")),
        "category": norm(deal.get("category")),
        "blob": norm(" ".join(str(p) for p in blob_parts if p)),
    }


def _match(rows: List[Dict[str, Any]], plan: _Plan) -> List[Dict[str, Any]]:
    """Filter + annotate rows with `_relevance`; keeps the incoming order."""
    prepared = []
    vocab: Set[str] = set()
    for deal in rows:
        fields = _fields(deal)
        word_set = set(fields["blob"][0].split())
        vocab.update(w for w in word_set if len(w) >= 3)
        prepared.append((deal, fields, word_set))
    plan.correct(sorted(vocab))

    weights_per_deal = []
    token_hits: Counter = Counter()
    for deal, fields, word_set in prepared:
        weights = {tok: _token_weight(tok, fields, word_set, plan) for tok in plan.tokens}
        for tok, w in weights.items():
            if w:
                token_hits[tok] += 1
        weights_per_deal.append((deal, fields, weights))

    # Words nothing in the catalogue matches ("cheap", "best", "offer") would
    # otherwise cap every deal's coverage — treat them as noise.
    live_tokens = [t for t in plan.tokens if token_hits[t]] or plan.tokens
    n = len(live_tokens)
    need = 1 if n <= 2 else math.ceil(n / 2)

    matched = []
    for deal, fields, weights in weights_per_deal:
        hit = [weights[t] for t in live_tokens if weights[t]]
        if len(hit) < need:
            continue
        rel = 60.0 * sum(hit) / n
        if n > 1 and len(hit) == n:
            rel += 20.0
        if plan.raw and len(plan.raw) > 2:
            if f" {plan.raw}" in fields["title"][1]:
                rel += 20.0
            elif f" {plan.raw}" in fields["blob"][1]:
                rel += 10.0
        deal["_relevance"] = round(rel, 1)
        matched.append(deal)
    return matched


def _candidates(
    *,
    store: str,
    brand: str,
    min_price: Optional[float],
    max_price: Optional[float],
    min_discount: int,
    channel_ids: Optional[List[int]],
    include_expired: bool,
    only_lowest: bool,
    order: str,
    archive: bool = False,
    has_coupon: bool = False,
) -> List[Dict[str, Any]]:
    """Light rows for every deal passing the hard filters (not category — that's counted)."""
    where: List[str] = []
    params: List[Any] = []
    if archive:
        # Past deals only (ended / expired / out of stock) — the Sheet-backed
        # archive shown under live results. Retired non-product posts stay out.
        where.append("(status != 'live' OR expires_at <= ?)")
        params.append(time.time())
        where.append("flags NOT LIKE '%not_a_deal%'")
    elif include_expired:
        where.append("status != 'dead'")
    else:
        where.append("status = 'live'")
        where.append("expires_at > ?")
        params.append(time.time())
    if store:
        where.append("store = ?")
        params.append(store.lower())
    if brand:
        where.append("LOWER(brand) = ?")
        params.append(brand.lower())
    if min_price is not None:
        where.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("price IS NOT NULL AND price <= ?")
        params.append(max_price)
    if min_discount:
        where.append("discount_pct >= ?")
        params.append(min_discount)
    if only_lowest:
        where.append("is_lowest = 1")
    if has_coupon:
        where.append("coupon IS NOT NULL AND coupon != ''")
    if channel_ids:
        where.append(f"channel_id IN ({','.join('?' for _ in channel_ids)})")
        params.extend(channel_ids)
    sql = (
        f"SELECT {_LIGHT_COLUMNS} FROM deals WHERE {' AND '.join(where)} "
        f"ORDER BY {order} LIMIT {_CANDIDATE_CAP}"
    )
    return [dict(r) for r in db.query(sql, params)]


def _rank(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda d: d["_relevance"] + float(d.get("score") or 0) * 0.45,
        reverse=True,
    )


def _load(page: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fetch full rows for one page in a single query, preserving order."""
    if not page:
        return []
    ids = [d["id"] for d in page]
    full = {
        d["id"]: d
        for d in db.rows_to_dicts(
            db.query(f"SELECT * FROM deals WHERE id IN ({','.join('?' for _ in ids)})", ids)
        )
    }
    out = []
    for light in page:
        deal = full.get(light["id"])
        if deal is not None:
            deal["_relevance"] = light.get("_relevance")
            out.append(deal)
    return out


def _counts(rows: List[Dict[str, Any]], column: str, key: str, lower: bool = False,
            limit: Optional[int] = None) -> List[Dict[str, Any]]:
    counter: Counter = Counter()
    for deal in rows:
        value = deal.get(column)
        if value:
            counter[value.lower() if lower else value] += 1
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit is not None:
        ordered = ordered[:limit]
    return [{key: name, "count": n} for name, n in ordered]


def search(
    *,
    q: str = "",
    category: str = "",
    subcategory: str = "",
    store: str = "",
    brand: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_discount: int = 0,
    channel_ids: Optional[List[int]] = None,
    include_expired: bool = False,
    only_lowest: bool = False,
    sort: str = "relevance",
    limit: int = 48,
    offset: int = 0,
    archive: bool = False,
    has_coupon: bool = False,
) -> Dict[str, Any]:
    rows = _candidates(
        store=store, brand=brand, min_price=min_price, max_price=max_price,
        min_discount=min_discount, channel_ids=channel_ids,
        include_expired=include_expired, only_lowest=only_lowest,
        order=SORTS.get(sort) or SORTS["best"], archive=archive, has_coupon=has_coupon,
    )

    plan = _Plan(q or "")
    if plan.tokens:
        rows = _match(rows, plan)
        if sort == "relevance":
            rows = _rank(rows)

    categories = _counts(rows, "category", "name")
    if category:
        rows = [d for d in rows if d.get("category") == category]
    if subcategory:
        rows = [d for d in rows if d.get("subcategory") == subcategory]

    total = len(rows)
    page = _load(rows[offset: offset + limit])
    return {
        "total": total,
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "results": [shape(d) for d in page],
        "categories": categories,
    }


def suggest(q: str, channel_ids: Optional[List[int]] = None, limit: int = 6) -> Dict[str, Any]:
    """Type-ahead: top deals plus the categories/brands/stores they fall under."""
    query = (q or "").strip()
    empty = {"query": query, "deals": [], "categories": [], "brands": [], "stores": []}
    if len(query) < 2:
        return empty
    plan = _Plan(query, typing=True)
    if not plan.tokens:
        return empty

    rows = _candidates(
        store="", brand="", min_price=None, max_price=None, min_discount=0,
        channel_ids=channel_ids, include_expired=False, only_lowest=False,
        order=SORTS["best"],
    )
    rows = _rank(_match(rows, plan))
    return {
        "query": query,
        "deals": [shape(d) for d in _load(rows[:limit])],
        "categories": _counts(rows, "category", "name", limit=5),
        "brands": _counts(rows, "brand", "key", lower=True, limit=5),
        "stores": _counts(rows, "store", "key", lower=True, limit=5),
    }


# BuyHatke resolves these hosts itself (verified); other shorteners (cuttli,
# bitli…) 404 there, so those need our resolved_url from the link checker.
_BUYHATKE_HOSTS = ("amazon.", "amzn.to", "amzn.in", "flipkart.com", "fkrt.", "myntra.com", "myntr.it",
                   "ajio.com", "nykaa.com", "tatacliq.com", "croma.com", "reliancedigital.in", "meesho.com")


def price_history_url(deal: Dict[str, Any]) -> str:
    for candidate in (deal.get("resolved_url"), deal.get("clean_url"), deal.get("url")):
        if candidate and any(host in candidate.lower() for host in _BUYHATKE_HOSTS):
            return "https://buyhatke.com/" + candidate
    return ""


def shape(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a DB row down to what the UI needs."""
    price = deal.get("price")
    mrp = deal.get("mrp")
    saving = None
    if price and mrp and mrp > price:
        saving = round(mrp - price)
    return {
        "id": deal.get("id"),
        "title": deal.get("title"),
        "price": price,
        "mrp": mrp,
        "saving": saving,
        "discount_pct": deal.get("discount_pct") or 0,
        "currency": deal.get("currency") or "INR",
        "store": deal.get("store"),
        "url": deal.get("url"),
        "image_url": deal.get("image_url"),
        "coupon": deal.get("coupon"),
        "category": deal.get("category"),
        "subcategory": deal.get("subcategory"),
        "brand": deal.get("brand"),
        "sizes": deal.get("sizes"),
        "channel_title": deal.get("channel_title"),
        "posted_at": deal.get("posted_at"),
        "expires_at": deal.get("expires_at"),
        "repost_count": deal.get("repost_count") or 1,
        "status": deal.get("status"),
        "score": deal.get("score"),
        "is_lowest": bool(deal.get("is_lowest")),
        "flags": deal.get("flags") or [],
        "price_history_url": price_history_url(deal),
        "relevance": deal.get("_relevance"),
    }


def facets(channel_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Counts for the filter sidebar, scoped to what the user can see."""
    now = time.time()
    where = ["status = 'live'", "expires_at > ?"]
    params: List[Any] = [now]
    if channel_ids:
        where.append(f"channel_id IN ({','.join('?' for _ in channel_ids)})")
        params.extend(channel_ids)
    clause = " AND ".join(where)

    def group(column: str) -> List[Dict[str, Any]]:
        rows = db.query(
            f"SELECT {column} AS key, COUNT(*) AS n FROM deals WHERE {clause} "
            f"AND {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY n DESC LIMIT 30",
            params,
        )
        return [{"key": r["key"], "count": r["n"]} for r in rows]

    price_row = db.query_one(
        f"SELECT MIN(price) AS lo, MAX(price) AS hi FROM deals WHERE {clause} AND price IS NOT NULL",
        params,
    )
    return {
        "categories": group("category"),
        "stores": group("store"),
        "brands": group("brand"),
        "channels": group("channel_title"),
        "price_range": {
            "min": price_row["lo"] if price_row and price_row["lo"] else 0,
            "max": price_row["hi"] if price_row and price_row["hi"] else 0,
        },
    }


def trending(channel_ids: Optional[List[int]] = None, limit: int = 12) -> List[Dict[str, Any]]:
    """Deals many channels reposted in the last day — the strongest signal we have."""
    now = time.time()
    where = ["status = 'live'", "expires_at > ?", "first_seen_at > ?", "repost_count > 1"]
    params: List[Any] = [now, now - 86400 * 2]
    if channel_ids:
        where.append(f"channel_id IN ({','.join('?' for _ in channel_ids)})")
        params.extend(channel_ids)
    rows = db.query(
        f"SELECT * FROM deals WHERE {' AND '.join(where)} "
        f"ORDER BY repost_count DESC, score DESC LIMIT ?",
        params + [limit],
    )
    return [shape(d) for d in db.rows_to_dicts(rows)]
