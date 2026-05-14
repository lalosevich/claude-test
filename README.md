# ccburn

A usage dashboard for Claude Code. Reads your local `~/.claude/projects/**/*.jsonl`
transcripts (no API calls, no network).

- **`ccburn_web.py`** — pretty browser dashboard (recommended)
- **`ccburn.py`** — terminal dashboard (fallback)
- **`ccburn_lib.py`** — shared logic (don't run directly)

## Install (one-time)

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python ccburn_web.py        # opens browser at http://127.0.0.1:8765
python ccburn.py            # one-shot terminal render
python ccburn.py --watch 5  # terminal, refresh every 5s
```

## Make the numbers match claude.ai

The first time you run this, the % won't perfectly line up with the official
dashboard at claude.ai (Anthropic doesn't publish their exact formula or limits).
**Calibrate it in 10 seconds:**

1. Open <https://claude.ai/settings/usage> and note your current %s
2. Run with `--calibrate-session` and `--calibrate-weekly`:

```bash
python ccburn.py --calibrate-session 16 --calibrate-weekly 38
```

That saves the right budgets to `~/.ccburn.json`. Future runs load it
automatically. Re-calibrate any time the numbers drift.

## Match the weekly reset

claude.ai's weekly limit resets on a specific day/hour (e.g. "Resets Mon 11:00 AM").
Tell ccburn yours:

```bash
python ccburn_web.py --week-reset-day 0 --week-reset-hour 11
```

`--week-reset-day`: `0` = Monday … `6` = Sunday. Hour is local time (0-23).
Save it permanently by including it in `--calibrate`:

```bash
python ccburn.py --calibrate-session 16 --calibrate-weekly 38 \
                 --week-reset-day 0 --week-reset-hour 11
```

## Plan presets (rough defaults if you skip calibration)

| Plan    | Session (weighted) | Weekly        |
|---------|--------------------|---------------|
| `pro`   |  1,000,000         |  10,000,000   |
| `max5`  |  4,500,000 (default) |  48,000,000 |
| `max20` | 18,000,000         | 192,000,000   |

```bash
python ccburn_web.py --plan pro
```

## All options

```
--claude-dir PATH       where ~/.claude lives (default: home)
--plan {pro,max5,max20} preset budgets
--session-limit N       override session weighted-token cap
--weekly-limit N        override weekly weighted-token cap
--week-reset-day 0-6    weekly reset weekday in local time (Mon=0)
--week-reset-hour 0-23  weekly reset hour in local time
--calibrate-session PCT current session % from claude.ai → save inferred cap
--calibrate-weekly PCT  current weekly  % from claude.ai → save inferred cap
--watch N               (terminal) refresh every N seconds
--port N                (web) HTTP port (default 8765)
--no-open               (web) don't auto-open browser
```

## How it works

Each assistant turn has a `message.usage` block. ccburn weights tokens by
Anthropic's pricing ratios so the totals roughly track real cost:

```
weighted = input + output × 5 + cache_creation × 1.25 + cache_read × 0.1
```

- **Session window:** anchored on the first message after the previous 5-hour
  window expired (matches Anthropic's behaviour, not a rolling 5h).
- **Weekly window:** fixed weekly schedule based on `--week-reset-day`/`-hour`.

Numbers are estimates, not 1:1 with Anthropic's official dashboard. Calibrate
(above) and they'll match closely.
