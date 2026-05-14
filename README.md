# ccburn

Two flavors of a usage dashboard for Claude Code. Both read your local
`~/.claude/projects/**/*.jsonl` transcripts (no API calls, no network).

- **`ccburn_web.py`** — pretty browser dashboard (recommended)
- **`ccburn.py`** — terminal dashboard (fallback, no browser needed)

## Install (one-time)

```bash
pip install -r requirements.txt
```

## Web dashboard (recommended)

```bash
python ccburn_web.py
```

A browser tab opens at <http://127.0.0.1:8765> with two animated panels
(session + weekly), gradient progress bars, and area charts. Auto-refreshes
every 5 seconds. Press Ctrl+C to stop.

Options:

```bash
python ccburn_web.py --plan max5     # default; also: pro, max20
python ccburn_web.py --port 9000
python ccburn_web.py --no-open       # don't auto-open browser
```

## Terminal dashboard (fallback)

```bash
python ccburn.py                     # one-shot render
python ccburn.py --watch 5           # refresh every 5 seconds
python ccburn.py --plan pro
```

## Configure limits

Token budgets are plan-dependent and not published by Anthropic. Defaults are
calibrated guesses per `--plan`:

| Plan    | Session (weighted tokens) | Weekly         |
|---------|---------------------------|----------------|
| `pro`   |   5,000,000               |   40,000,000   |
| `max5`  |  30,000,000 (default)     |  250,000,000   |
| `max20` | 120,000,000               | 1,000,000,000  |

Override with flags or env vars:

```bash
python ccburn_web.py --session-limit 50000000 --weekly-limit 400000000
# or
export CCBURN_SESSION_LIMIT=50000000
export CCBURN_WEEKLY_LIMIT=400000000
```

## How it works

Each assistant turn in your local transcripts has a `message.usage` block.
ccburn applies Anthropic's pricing-equivalent weighting:

```
weighted = input + output × 5 + cache_creation × 1.25 + cache_read × 0.1
```

Cache reads dominate raw counts but only cost ~10% of an input token, so
weighting them avoids massive over-counting. The session window anchors on
the first record in the last 5 hours; weekly anchors on the first record
in the last 7 days.

Numbers won't perfectly match claude.ai's official dashboard (their formula
isn't public), but trends should track. Tune `--session-limit` /
`--weekly-limit` until the percentages match your real ones.
