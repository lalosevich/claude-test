# ccburn

Terminal usage dashboard for Claude Code, inspired by the ccburn screenshots
floating around r/ClaudeAI. Reads your local `~/.claude/projects/**/*.jsonl`
session transcripts (no API calls, no network) and plots:

- **Session (5h):** budget pace, cumulative usage, projection, and a "now" marker
  for the current rolling 5-hour window.
- **Weekly (7d):** the same, over the last 7 days.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python ccburn.py                  # one-shot render
python ccburn.py --watch 5        # refresh every 5 seconds
python ccburn.py --width 70 --height 18
```

## Configure limits

Token budgets are plan-dependent and not publicly documented, so they are
exposed as flags / env vars. Defaults are rough guesses for a Max plan.

```bash
export CCBURN_SESSION_LIMIT=2000000     # Pro-ish
export CCBURN_WEEKLY_LIMIT=20000000
python ccburn.py
```

Or via flags: `--session-limit`, `--weekly-limit`.

## How it works

For every line in every JSONL transcript, ccburn reads `message.usage` and sums
`input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
Duplicate `message.id`s are deduped across files. The 5h session anchors on the
first record in the last 5h; weekly anchors on the first record in the last 7d.
