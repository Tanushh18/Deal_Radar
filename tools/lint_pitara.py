"""Lint the notification pitara: python tools/lint_pitara.py [path]

Fails (exit 1) on anything that could ship a broken, misleading or unsafe
notification. It checks the JSON, not the humour — that's for a human.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List

DEFAULT = Path(__file__).resolve().parent.parent / "app" / "content" / "notification_pitara.json"

# Stand-in lengths for placeholders, so the length check reflects a real push.
SAMPLE = {"name": 30, "price": 7, "mrp": 7, "save": 7, "discount": 2, "store": 8, "brand": 10,
          "category": 14, "day": 9}
TITLE_MAX, BODY_MAX = 62, 130
NEEDS = {"price", "mrp", "discount", "brand", "store", "endsSoon"}
TONES = {"tease", "dost", "self", "heart"}
TIMES = {"morning", "afternoon", "evening", "night"}

# Fake urgency / scarcity (dark patterns) — only allowed when tied to real data
# (endsSoon), never as free-floating copy.
URGENCY = ["selling fast", "limited stock", "only few left", "few left", "last chance", "hurry",
           "before it's gone", "before its gone", "going fast", "almost sold out", "act now",
           "tap before", "won't last", "wont last", "har koi le raha", "sab le rahe"]
# Things we never joke about, and real brands/celebs/trademarks we never name.
BANNED = ["mota", "motapa", "moti ", "kaala", "kali ", "gora", "fat ", "ugly", "bhikari", "garib",
          "hindu", "muslim", "christian", "sikh", "dalit", "brahmin", "pakistan", "modi", "rahul",
          "depress", "suicide", "anxiety", "cancer", "breakup", "divorce", "single ",
          "ipl", "bcci", "virat", "dhoni", "rohit", "kohli", "srk", "salman", "alia", "deepika",
          "amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa", "zara", "h&m", "nike", "adidas"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def expand(text: str) -> int:
    return len(PLACEHOLDER.sub(lambda m: "x" * SAMPLE.get(m.group(1), 10), text))


def lint(path: Path = DEFAULT) -> List[str]:
    errors: List[str] = []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read {path}: {exc}"]
    known = set(doc.get("meta", {}).get("placeholders", SAMPLE))
    seen_ids: Dict[str, int] = {}
    seen_text: Dict[str, str] = {}
    for i, t in enumerate(doc.get("templates", [])):
        tid = t.get("id") or f"#{i}"

        def bad(msg: str) -> None:
            errors.append(f"{tid}: {msg}")

        if tid in seen_ids:
            bad("duplicate id")
        seen_ids[tid] = i
        title, body = t.get("title", ""), t.get("body", "")
        if not title or not body:
            bad("missing title or body")
            continue
        used = set(PLACEHOLDER.findall(title + " " + body))
        for p in used - known:
            bad(f"unknown placeholder {{{p}}}")
        for p in used & {"price", "mrp", "discount", "brand", "store"}:
            if p not in t.get("needs", []):
                bad(f"uses {{{p}}} but 'needs' doesn't list it")
        if "save" in used and not {"mrp", "price"} <= set(t.get("needs", [])):
            bad("uses {save} but 'needs' lacks mrp and price")
        for n in t.get("needs", []):
            if n not in NEEDS:
                bad(f"unknown need {n!r}")
        if t.get("tone") not in TONES:
            bad(f"tone must be one of {sorted(TONES)}")
        for tm in t.get("time", []):
            if tm not in TIMES:
                bad(f"unknown time {tm!r}")
        if expand(title) > TITLE_MAX:
            bad(f"title too long ({expand(title)} > {TITLE_MAX})")
        if expand(body) > BODY_MAX:
            bad(f"body too long ({expand(body)} > {BODY_MAX})")
        low = f" {title} {body} ".lower()
        for w in URGENCY:
            if w in low and "endsSoon" not in t.get("needs", []):
                bad(f"fake-urgency phrase {w!r} (only allowed with needs: endsSoon)")
        for w in BANNED:
            if re.search(rf"(?<![a-z]){re.escape(w.strip())}(?![a-z])" if w.endswith(" ") else re.escape(w), low):
                bad(f"banned word {w.strip()!r}")
        key = re.sub(r"\W+", "", low)
        if key in seen_text:
            bad(f"same text as {seen_text[key]}")
        seen_text[key] = tid
    return errors


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    problems = lint(target)
    try:
        count = len(json.loads(target.read_text(encoding="utf-8")).get("templates", []))
    except (OSError, ValueError):
        count = 0
    for p in problems:
        print("FAIL", p)
    print(f"{count} templates, {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
