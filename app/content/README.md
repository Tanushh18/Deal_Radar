# Notification copy (the "pitara")

Every notification the server writes itself — hot deal, crazy deal, nudge, price drop, follow, daily digest,
weekly pick, watchlist alert — picks its title and body from the lines in this folder. The app downloads the
same lines (`GET /api/notification-templates`) and writes its own notifications from them.

```
src/*.txt                 one line per template:  mood|tone|title|body|flags     (edit these)
notification_pitara*.json built from src by tools/build_pitara.py                 (commit both)
```

```
python tools/build_pitara.py     # src/*.txt -> notification_pitara_*.json  ("needs" is worked out from the placeholders)
python tools/lint_pitara.py      # placeholders, lengths, fake urgency, banned words, repeats across files
python -m tests.test_pitara      # who may say what, variety, the endpoint
```

(`notification_pitara.json` itself — the first Women Fashion batch — is edited by hand; there is no source for it.)

## What a line may say

A line is only used when everything it claims is true for that deal: `needs` (price, mrp, discount, brand, store,
lowest, endsSoon), `min_discount`, category, time of day, day of week, and any context value it uses
(`{count}`, `{value}`, `{query}`, `{target}`…). A "lowest price" line only ever goes out for a genuine lowest price;
nothing claims stock, deadlines or delivery. `personal` lines run on the phone only. See `app/services/pitara.py`.

`pitara_lint.py` holds the rules. Both the shipped files and anything the model drafts go through them.

## Drafts by the model (optional)

`app/services/pitara_writer.py` can draft new lines overnight, for review, in the admin panel
(Notifications → "Notification copy (AI drafts)"). The model writes *templates* with placeholders — never numbers,
prices or claims — and only lines that pass the stricter `check_drafted` rules and aren't repeats are kept.
Nothing goes live until it's approved, unless auto-publish is switched on (off by default).

It shares the Groq key and daily budget with deal enrichment and sale blurbs, so it takes a small slice:
its own tokens-per-day ceiling, and no run at all once the shared budget is mostly used
(`PITARA_WRITER_*` in `.env.example`). Approved lines are stored in the database (`meta`, mirrored to MongoDB)
and merged into the pitara; any line, shipped or drafted, can be switched off by id from the same card.

`NOTIFICATION_PITARA_ENABLED=false` goes back to the built-in text everywhere.
