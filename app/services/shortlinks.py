"""Short affiliate links on our own domain: /a/<ASIN> (Amazon) and /f/<pid> (Flipkart).

Stateless — the product id in the path is all that is needed to rebuild the affiliate link, so
nothing is stored, any server can answer, and links never expire. Third-party shorteners are
avoided on purpose: some re-route Amazon clicks through their own monetiser.
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import settings
from . import links, parser

ASIN = re.compile(r"[A-Z0-9]{10}")
PID = re.compile(r"[A-Z0-9]{8,32}")


def short_url(store: str, plain: str) -> Optional[str]:
    """`https://<our domain>/a/<ASIN>` or `/f/<pid>` for a plain store link; None when it can't be done
    (no public URL configured, or the link isn't a single product we can name by id)."""
    base = settings.public_url
    if not base:
        return None
    if store == "amazon":
        m = parser.ASIN_RE.search(plain)
        return f"{base}/a/{m.group(1).upper()}" if m else None
    if store == "flipkart":
        m = parser.FK_PID_RE.search(plain)
        return f"{base}/f/{m.group(1).upper()}" if m else None
    return None


def target(store: str, ident: str) -> Optional[str]:
    """The affiliate link a short link stands for; None if the id isn't well formed."""
    ident = ident.upper()
    if store == "a" and ASIN.fullmatch(ident):
        return links.with_affiliate(f"https://www.amazon.in/dp/{ident}")
    if store == "f" and PID.fullmatch(ident):
        return links.with_affiliate(f"https://www.flipkart.com/product/p/itme?pid={ident}")
    return None
