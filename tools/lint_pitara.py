"""Lint the notification pitara: python tools/lint_pitara.py [file ...]

With no arguments, lints every app/content/notification_pitara*.json together
(so duplicate ids/text are caught across files). Fails (exit 1) on anything
that could ship a broken, misleading or unsafe notification. It checks the
JSON, not the humour — that's for a human. The rules live in
app/services/pitara_lint.py, shared with the writer that drafts new lines.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.pitara_lint import lint  # noqa: E402

CONTENT = ROOT / "app" / "content"


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
