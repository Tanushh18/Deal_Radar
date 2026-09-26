"""Turn a raw Telegram message into a structured Deal.

Channel posts are messy and inconsistent:

    🔥🔥 LOOT DEAL 🔥🔥
    boAt Rockerz 450 Bluetooth On Ear Headphone
    ₹1,299 (MRP ₹2,990) — 56% OFF
    Use code SAVE10
    https://amzn.to/3xYzAbc

Everything here is defensive: any field can be missing, and a message that
yields no price *and* no link is not a deal at all.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from . import taxonomy

# Bump whenever price/MRP extraction changes: stored deals are re-parsed from
# their raw text once per version (store.reparse_stored_deals).
PARSER_VERSION = 4

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# ₹1,299  |  Rs. 1299  |  INR 1299  |  1299/-  |  @999
# The optional dot sits *after* \b: writing `\brs\.?\b` lets the regex backtrack
# off the period, and then \s* can't skip it, so "Rs. 999" never matches.
PRICE_RE = re.compile(
    r"(?:₹|\brs\b\.?|\binr\b\.?|\bmrp\b\.?|@)\s*([0-9][0-9,]{1,7}(?:\.[0-9]{1,2})?)"
    r"|([0-9][0-9,]{1,7})\s*(?:/-|\brs\b)",
    re.IGNORECASE,
)
DISCOUNT_RE = re.compile(r"(\d{1,3})\s*%\s*(?:off|discount|dis)", re.IGNORECASE)

# These channels quote the selling price as "@7,371", "@ ₹146" or "at ₹34,740".
# It is by far the most reliable signal in the post, and critically it is the
# number attached to the product named in the title — unlike "smallest number
# present", which is whatever coupon happens to be mentioned.
AT_PRICE_RE = re.compile(
    r"(?:@|\bat\b|\bjust\b|\bonly\b)\s*(?:₹|\brs\b\.?|\binr\b\.?)?\s*"
    r"([0-9][0-9,]{2,7}(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)

# "Printed T-Shirt - ₹91", "Mugs Set | ₹244": a currency amount right after a
# separator on the product-name line is that product's price.
TITLE_PRICE_RE = re.compile(
    r"[-–—|:]\s*(?:₹|\brs\b\.?|\binr\b\.?)\s*([0-9][0-9,]{1,7}(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
# "Watches from ₹509", "Jeans starts @208" — a floor, not the price of one item.
FROM_PRICE_RE = re.compile(
    r"\b(?:from|starts?(?:\s+(?:from|at))?|starting(?:\s+(?:from|at))?)\s*(?:just\s*)?"
    r"(?:@|₹|\brs\b\.?|\binr\b\.?)?\s*[0-9]",
    re.IGNORECASE,
)
UPTO_DISCOUNT_RE = re.compile(r"\b(?:up\s*to|upto|till)\s*(\d{1,3})\s*%", re.IGNORECASE)
MIN_BUY_RE = re.compile(r"\bmin(?:imum)?\.?\s*(?:buy|order|qty|quantity)\s*[-:]?\s*(\d{1,2})\b", re.IGNORECASE)
# Lines that are channel furniture, never a product name.
JUNK_LINE_RE = re.compile(
    r"deal\s*time|buy\s*now|shop\s*now|grab\s*now|order\s*now|click\s*here|^more\b|"
    r"min(?:imum)?\.?\s*(?:buy|order)|join\s+(?:us|our|now)|share\s+(?:and|&)|^link\b",
    re.IGNORECASE,
)

# Money that is a *reduction*, not a price: "₹819 off with HDFC Credit Card",
# "Apply ₹3500 Coupon", "flat ₹200 off", "₹1250 cashback".
#
# Removing these before reading prices is the single most important step in
# this file. Without it a ₹34,740 pair of headphones posted with an "Apply
# ₹2000 off Coupon" line is stored as a ₹2,000 product at 94% off — the
# coupon becomes the price and the real price becomes a fabricated MRP.
DISCOUNT_AMOUNT_RE = re.compile(
    r"(?:apply\s+)?(?:flat\s+|extra\s+|additional\s+)?"
    r"(?:₹|\brs\b\.?|\binr\b\.?)?\s*[0-9][0-9,]{1,7}\s*(?:/-)?\s*"
    r"(?:off|discount|cashback|coupon)\b"
    r"|(?:coupon|cashback|discount)\s*(?:of|:)?\s*(?:₹|\brs\b\.?|\binr\b\.?)?\s*[0-9][0-9,]{1,7}",
    re.IGNORECASE,
)
MRP_RE = re.compile(
    r"(?:mrp|m\.r\.p|was|list price|original)\D{0,12}?([0-9][0-9,]{1,7})", re.IGNORECASE
)
COUPON_RE = re.compile(
    # Codes may contain inner hyphens ("GBOULT-100"), never a leading/trailing one.
    r"(?:coupon|code|promo|voucher)\s*[:\-]?\s*[\"']?([A-Z0-9](?:[A-Z0-9-]{2,16}[A-Z0-9]))[\"']?", re.IGNORECASE
)
SIZE_RE = re.compile(
    r"\b(?:size[s]?\s*[:\-]?\s*)((?:(?:XS|S|M|L|XL|XXL|XXXL|2XL|3XL|\d{1,2})[,\s/&]*){1,10})",
    re.IGNORECASE,
)
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|d)/([A-Z0-9]{10})", re.IGNORECASE)
FK_PID_RE = re.compile(r"[?&]pid=([A-Z0-9]+)", re.IGNORECASE)
FK_ITM_RE = re.compile(r"/p/(itm[a-z0-9]+)", re.IGNORECASE)
MYNTRA_ID_RE = re.compile(r"myntra\.com/.*?/(\d{6,})(?:/buy)?/?(?:[?#]|$)", re.IGNORECASE)
AJIO_ID_RE = re.compile(r"ajio\.com/(?:.*/)?p/([0-9a-z_]{6,})", re.IGNORECASE)
NYKAA_ID_RE = re.compile(r"nykaa\.com/.*/p/(\d{4,})", re.IGNORECASE)
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️‍]+"
)

# Affiliate / tracking params to strip so the same product from two channels
# collapses to one canonical URL.
TRACKING_PARAMS = {
    "tag", "ref", "ref_", "linkcode", "linkid", "ascsubtag", "creative", "creativeasin",
    "camp", "affid", "affextparam1", "affextparam2", "th", "psc", "smid", "pf_rd_r",
    "pf_rd_p", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "content-id", "qid", "sr", "sprefix",
    "keywords", "crid", "_encoding", "affid", "affExtParam1", "lid", "marketplace",
    "store", "srno", "otracker", "fm", "iid", "ppt", "ppn", "ssid", "cmpid",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "irgwc", "clickid", "subid", "sid", "aff_id", "offer_id",
}

TITLE_NOISE = re.compile(
    r"\b(?:loot|lut|deal|deals|offer|offers|best price|cheapest|hurry|fast|limited|"
    r"stock|running|live|hot|new|steal|mega|big|flash|today only|grab|buy now|link|"
    r"price drop|lowest|all time low|atl|checkout|bank offer)\b",
    re.IGNORECASE,
)
STOPWORDS = {
    "the", "and", "for", "with", "from", "pack", "of", "set", "combo", "free", "off",
    "buy", "get", "new", "with", "size", "pcs", "piece", "in", "at", "on", "to",
}


def _to_number(raw: str) -> Optional[float]:
    try:
        value = float(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
    # Guard against phone numbers / pincodes / mAh figures masquerading as prices.
    if value <= 0 or value > 5_000_000:
        return None
    return value


def strip_discount_amounts(text: str) -> str:
    """Blank out "₹N off"-style amounts so they can't be mistaken for prices."""
    return DISCOUNT_AMOUNT_RE.sub(" ", text or "")


def extract_prices(text: str) -> List[float]:
    prices: List[float] = []
    for match in PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        value = _to_number(raw)
        if value is not None:
            prices.append(value)
    return prices


def extract_quoted_price(text: str) -> Optional[float]:
    """The "@…" / "at …" selling price, if the post quotes one.

    Returns the *first* such price: a post listing several products puts the
    one named in the title first, so the first quote is the one that belongs
    with the title we extracted.
    """
    for match in AT_PRICE_RE.finditer(text or ""):
        value = _to_number(match.group(1))
        if value is not None:
            return value
    return None


def detect_store(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().lstrip("www.")
    for store, domains in taxonomy.STORE_DOMAINS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain) or domain in host:
                return store
    return host.split(".")[0] if host else "unknown"


def clean_url(url: str) -> str:
    """Strip affiliate/tracking noise so identical products collapse together."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    kept = {}
    for key, values in parse_qs(parts.query, keep_blank_values=False).items():
        if key.lower() in {p.lower() for p in TRACKING_PARAMS}:
            continue
        kept[key] = values[0]
    query = "&".join(f"{k}={v}" for k, v in sorted(kept.items()))
    return urlunparse((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", query, ""))


def product_key(url: str, store: str, norm_title: str) -> str:
    """Stable identity for a product across channels.

    Prefers a real marketplace product id; falls back to a normalised title
    hash, which is what makes dedup work for shortened/unresolvable links.
    """
    asin = ASIN_RE.search(url)
    if asin:
        return f"amazon:{asin.group(1).upper()}"
    pid = FK_PID_RE.search(url)
    if pid:
        return f"flipkart:{pid.group(1).upper()}"
    itm = FK_ITM_RE.search(url)
    if itm:
        return f"flipkart:{itm.group(1).lower()}"
    for store_name, pattern in (("myntra", MYNTRA_ID_RE), ("ajio", AJIO_ID_RE), ("nykaa", NYKAA_ID_RE)):
        found = pattern.search(url)
        if found:
            return f"{store_name}:{found.group(1).lower()}"
    if norm_title:
        digest = hashlib.sha1(norm_title.encode("utf-8")).hexdigest()[:16]
        return f"{store}:t:{digest}"
    return f"{store}:u:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"


def normalize_title(title: str) -> str:
    text = EMOJI_RE.sub(" ", title or "").lower()
    text = TITLE_NOISE.sub(" ", text)
    text = re.sub(r"(?:₹|\brs\.?\b|\binr\b)\s*[0-9,]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in STOPWORDS and len(t) > 1]
    return " ".join(tokens[:10])


def _clean_line(line: str) -> str:
    line = EMOJI_RE.sub("", line)
    line = URL_RE.sub("", line)
    line = re.sub(r"[*_`~]+", "", line)          # markdown leftovers
    line = re.sub(r"^[\s\-–—•·>»|]+", "", line)
    return re.sub(r"\s{2,}", " ", line).strip(" -–—•·|")


def extract_title(text: str) -> str:
    """Pick the line that most looks like a product name."""
    candidates: List[str] = []
    for raw_line in (text or "").splitlines():
        line = _clean_line(raw_line)
        if len(line) < 6 or JUNK_LINE_RE.search(line):
            continue
        letters = sum(c.isalpha() for c in line)
        if letters < 5:
            continue
        # A line that is mostly price/percent is not a title.
        if re.fullmatch(r"[^a-zA-Z]*", line):
            continue
        digits = sum(c.isdigit() for c in line)
        if digits > letters:
            continue
        candidates.append(line)
        if len(candidates) >= 4:
            break
    if not candidates:
        # No product name anywhere ("₹199 : Buy Now … Deal Time: 12:24 PM") —
        # a card titled with a timestamp is worse than no card.
        return ""
    # Prefer the first reasonably long candidate, else the longest available.
    for candidate in candidates:
        if len(candidate) >= 18:
            return candidate[:180]
    return max(candidates, key=len)[:180]


def extract_sizes(text: str) -> str:
    match = SIZE_RE.search(text or "")
    if not match:
        return ""
    sizes = re.split(r"[,\s/&]+", match.group(1).strip())
    return ", ".join(s.upper() for s in sizes if s)[:80]


_NOT_A_CODE = {
    "CODE", "COUPON", "PROMO", "APPLY", "OFFER", "HTTPS", "HTTP", "WWW", "LINK", "CLICK", "HERE",
    "BELOW", "ABOVE", "WITH", "FROM", "USE", "AVAIL", "CHECK", "APPLIED", "AUTO", "ONLY", "CART",
    "PAGE", "DETAILS", "EXTRA", "BANK", "CARD", "NEEDED", "REQUIRED", "AVAILABLE",
}


def extract_coupon(text: str) -> str:
    text = text or ""
    for match in COUPON_RE.finditer(text):
        raw = match.group(1)
        code = raw.upper()
        # "coupon: https://…" or "code amzn.to/…" — the start of a link, not a code.
        if text[match.end(1):match.end(1) + 3].startswith((":/", ".")):
            continue
        # Reject pure numbers (usually a price) and ordinary words.
        if code.isdigit() or code in _NOT_A_CODE:
            continue
        # Real codes are written in capitals ("EK20", "FURNITURE") or carry a
        # digit ("save10"); a lowercase word after "coupon" is just prose.
        if any(c.isdigit() for c in raw) or raw.isupper():
            return code
    return ""


def deal_id(pkey: str, price: Optional[float]) -> str:
    return hashlib.sha1(f"{pkey}|{int(price or 0)}".encode("utf-8")).hexdigest()[:20]


def build_search_blob(deal: Dict[str, Any]) -> str:
    return " ".join(filter(None, [
        (deal.get("title") or "").lower(), deal.get("norm_title") or "", (deal.get("brand") or "").lower(),
        (deal.get("category") or "").lower(), (deal.get("subcategory") or "").lower(), deal.get("store") or "",
    ]))


def rebase_on_url(deal: Dict[str, Any], final_url: str) -> None:
    """Re-derive store / product identity from a resolved store URL, in place.

    The posted link (deal["url"]) is kept — it is what the channel shared and
    it works; the resolved one only fixes the store and, when it carries an
    ASIN / Flipkart pid, the product key that dedup and price history hang on.
    """
    store = detect_store(final_url)
    deal["resolved_url"] = final_url
    deal["store"] = store
    deal["clean_url"] = clean_url(final_url)
    deal["product_key"] = product_key(final_url, store, deal.get("norm_title") or "")
    deal["id"] = deal_id(deal["product_key"], deal.get("price"))
    deal["search_blob"] = build_search_blob(deal)


def parse_message(
    text: str,
    *,
    channel_id: int,
    channel_title: str,
    message_id: int,
    posted_at: float,
    image_url: str = "",
    ttl_hours: int = 96,
) -> Optional[Dict[str, Any]]:
    """Parse one message. Returns None when it isn't a usable deal."""
    text = (text or "").strip()
    if len(text) < 10 or taxonomy.is_spam(text):
        return None

    urls = URL_RE.findall(text)
    # Prices are read from a copy with "₹N off"/"apply ₹N coupon" removed;
    # the original text is kept for the title, coupon code and raw display.
    sanitized = strip_discount_amounts(text)
    prices = extract_prices(sanitized)
    quoted = extract_quoted_price(sanitized)
    if not urls and not prices and quoted is None:
        return None  # neither a link nor a price -> chatter, not a deal

    title = extract_title(text)
    if not title:
        return None
    title_price = None
    title_match = TITLE_PRICE_RE.search(strip_discount_amounts(title))
    if title_match:
        title_price = _to_number(title_match.group(1))

    url = urls[0] if urls else ""
    store = detect_store(url) if url else "unknown"
    cleaned = clean_url(url) if url else ""

    # --- price / MRP resolution -------------------------------------
    price: Optional[float] = None
    mrp: Optional[float] = None
    mrp_match = MRP_RE.search(sanitized)
    if mrp_match:
        mrp = _to_number(mrp_match.group(1))

    if quoted is not None:
        # An explicitly quoted "@…" price wins over anything inferred.
        price = quoted
    elif title_price is not None:
        price = title_price
    elif prices:
        candidates = sorted(set(prices))
        if mrp is not None:
            below = [p for p in candidates if p < mrp]
            price = below[-1] if below else candidates[0]
        else:
            price = candidates[0]

    # An MRP is only ever taken from an explicit "MRP/was/original" mention.
    # The old "two prices in a post means deal + MRP" rule is what fabricated
    # the 90%-off entries: in a post listing several products, or one quoting
    # a coupon, the largest number belongs to something else entirely.
    if mrp is not None and price is not None and mrp <= price:
        mrp = None

    discount = 0
    discount_match = DISCOUNT_RE.search(text)
    if discount_match:
        discount = int(discount_match.group(1))
    elif price and mrp and mrp > price:
        discount = int(round((mrp - price) / mrp * 100))

    # Shown on the card as "From ₹509" / "Up to 87% off" / "Min 3" instead of
    # presenting a range or a per-unit figure as one exact product price.
    flags: List[str] = []
    if price is not None and FROM_PRICE_RE.search(text):
        flags.append("price_from")
    if UPTO_DISCOUNT_RE.search(text):
        flags.append("upto_discount")
    min_buy = MIN_BUY_RE.search(text)
    if min_buy and int(min_buy.group(1)) > 1:
        flags.append(f"min_buy_{int(min_buy.group(1))}")

    norm = normalize_title(title)
    category, subcategory = taxonomy.classify(f"{title} {text[:400]}")
    brand = taxonomy.detect_brand(f"{title} {text[:200]}")
    pkey = product_key(url or title, store, norm)

    now = time.time()
    search_blob = build_search_blob(
        {"title": title, "norm_title": norm, "brand": brand, "category": category,
         "subcategory": subcategory, "store": store}
    )

    return {
        "id": deal_id(pkey, price),
        "title": title,
        "norm_title": norm,
        "product_key": pkey,
        "price": price,
        "mrp": mrp,
        "discount_pct": max(0, min(discount, 99)),
        "currency": "INR",
        "store": store,
        "url": url,
        "clean_url": cleaned,
        "image_url": image_url or "",
        "coupon": extract_coupon(text),
        "category": category,
        "subcategory": subcategory,
        "brand": brand,
        "sizes": extract_sizes(text),
        "channel_id": channel_id,
        "channel_title": channel_title,
        "message_id": message_id,
        "posted_at": posted_at or now,
        "first_seen_at": now,
        "last_seen_at": now,
        "expires_at": (posted_at or now) + ttl_hours * 3600,
        "repost_count": 1,
        "channels_seen": [channel_id],
        "status": "live",
        "score": 0.0,
        "is_lowest": 0,
        "flags": flags,
        "raw_text": text[:1500],
        "search_blob": search_blob,
    }
