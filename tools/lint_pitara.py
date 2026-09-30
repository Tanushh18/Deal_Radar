"""Lint the notification pitara: python tools/lint_pitara.py [file ...]

With no arguments, lints every app/content/notification_pitara*.json together
(so duplicate ids/text are caught across files). Fails (exit 1) on anything
that could ship a broken, misleading or unsafe notification. It checks the
JSON, not the humour — that's for a human.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List

CONTENT = Path(__file__).resolve().parent.parent / "app" / "content"

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


def expand(text: str) -> int:
    return len(PLACEHOLDER.sub(lambda m: "x" * SAMPLE.get(m.group(1), 10), text))


def lint(paths: List[Path]) -> List[str]:
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

            def bad(msg: str) -> None:
                errors.append(f"{tid}: {msg}")

            if tid in seen_ids:
                bad(f"duplicate id (also in {seen_ids[tid]})")
            seen_ids[tid] = path.name
            title, body = t.get("title", ""), t.get("body", "")
            if not title or not body:
                bad("missing title or body")
                continue
            needs = set(t.get("needs", []))
            used = set(PLACEHOLDER.findall(title + " " + body))
            for p in used - set(SAMPLE):
                bad(f"unknown placeholder {{{p}}}")
            for p in used & {"price", "mrp", "discount", "brand", "store"}:
                if p not in needs:
                    bad(f"uses {{{p}}} but 'needs' doesn't list it")
            if "save" in used and not {"mrp", "price"} <= needs:
                bad("uses {save} but 'needs' lacks mrp and price")
            if used & CONTEXT and not t.get("kinds"):
                bad(f"uses context placeholder {sorted(used & CONTEXT)} but has no 'kinds'")
            for n in needs:
                if n not in NEEDS:
                    bad(f"unknown need {n!r}")
            for k in t.get("kinds", []):
                if k not in KINDS:
                    bad(f"unknown kind {k!r}")
            if t.get("tone") not in TONES:
                bad(f"tone must be one of {sorted(TONES)}")
            md = t.get("min_discount")
            if md is not None and (not isinstance(md, int) or not 1 <= md <= 95 or "discount" not in needs):
                bad("min_discount must be 1-95 and needs must include discount")
            for tm in t.get("time", []):
                if tm not in TIMES:
                    bad(f"unknown time {tm!r}")
            if expand(title) > TITLE_MAX:
                bad(f"title too long ({expand(title)} > {TITLE_MAX})")
            if expand(body) > BODY_MAX:
                bad(f"body too long ({expand(body)} > {BODY_MAX})")
            low = f" {title} {body} ".lower()
            for w in URGENCY:
                if w in low and "endsSoon" not in needs:
                    bad(f"fake-urgency phrase {w!r} (only allowed with needs: endsSoon)")
            for w in BANNED:
                if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low):
                    bad(f"banned word {w!r}")
            key = re.sub(r"\W+", "", low)
            if key in seen_text:
                bad(f"same text as {seen_text[key]}")
            seen_text[key] = tid
    return errors


def main(argv: List[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(CONTENT.glob("notification_pitara*.json"))
    problems = lint(paths)
    count = 0
    for p in paths:
        try:
            count += len(json.loads(p.read_text(encoding="utf-8")).get("templates", []))
        except (OSError, ValueError):
            pass
    for p in problems:
        print("FAIL", p)
    print(f"{len(paths)} file(s), {count} templates, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
