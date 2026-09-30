"""Compile pitara sources into JSON: python tools/build_pitara.py

Reads app/content/src/*.txt and writes app/content/notification_pitara_<name>.json.
The source format keeps writing 1000+ lines painless — `needs` is worked out
from the placeholders, so it can't drift out of sync with the text.

  # category: Men Fashion        (header; "Any" for kind-specific files)
  # prefix: mf                   (id prefix -> mf001, mf002 ...)
  # kinds: nudge                 (optional; default kinds for every line)
  mood|tone|title|body|flags     (flags optional, space separated)

flags:  t=morning,night   time of day        d=weekday | d=weekend
        w=0,6             weekdays (0=Sunday) p             on-device personal line
        e                 needs endsSoon      L  needs is_lowest (claims 'lowest price')
        md=50             only when discount >= 50 (lines that call a discount big/rare)
        k=price_drop      kinds (comma separated)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "content"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
NEED_OF = {"price": ["price"], "mrp": ["mrp"], "save": ["mrp", "price"], "discount": ["discount"],
           "brand": ["brand"], "store": ["store"]}
META = {"version": 1, "placeholders": ["name", "price", "mrp", "save", "discount", "store", "brand", "category", "day",
                                       "target", "value", "count", "query", "coupon", "sale", "when"]}


def build(src: Path) -> Path:
    header, templates = {}, []
    for n, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            key, _, value = line[1:].partition(":")
            header[key.strip()] = value.strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (4, 5):
            raise SystemExit(f"{src.name}:{n}: expected mood|tone|title|body[|flags], got {len(parts)} fields")
        mood, tone, title, body = parts[:4]
        flags = parts[4].split() if len(parts) == 5 else []
        used = PLACEHOLDER.findall(title + " " + body)
        needs = []
        for p in used:
            for need in NEED_OF.get(p, []):
                if need not in needs:
                    needs.append(need)
        t = {"id": f"{header['prefix']}{len(templates) + 1:03d}", "category": header.get("category", "Any"),
             "mood": mood, "tone": tone, "title": title, "body": body, "needs": needs}
        kinds = [k for k in header.get("kinds", "").split(",") if k.strip()]
        for f in flags:
            if f == "p":
                t["personal"] = True
            elif f == "e":
                t["needs"].append("endsSoon")
            elif f == "L":
                t["needs"].append("lowest")
            elif f.startswith("t="):
                t["time"] = f[2:].split(",")
            elif f.startswith("d="):
                t["day"] = f[2:]
            elif f.startswith("w="):
                t["weekdays"] = [int(x) for x in f[2:].split(",")]
            elif f.startswith("md="):
                t["min_discount"] = int(f[3:])
                if "discount" not in t["needs"]:
                    t["needs"].append("discount")
            elif f.startswith("k="):
                kinds = f[2:].split(",")
            else:
                raise SystemExit(f"{src.name}:{n}: unknown flag {f!r}")
        if kinds:
            t["kinds"] = [k.strip() for k in kinds]
        templates.append(t)
    out = ROOT / f"notification_pitara_{src.stem}.json"
    doc = {"meta": dict(META, about=f"Pitara: {header.get('category', 'Any')} ({src.stem}). Built from src/{src.name}."),
           "templates": templates}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    sources = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "src").glob("*.txt"))
    for s in sources:
        out = build(s)
        print(f"{s.name} -> {out.name}")
