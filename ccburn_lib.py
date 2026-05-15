"""Shared logic for ccburn (terminal) and ccburn_web (browser).

Reads ~/.claude/projects/**/*.jsonl, sums cost-weighted tokens per assistant
turn, and exposes session/weekly window helpers.
"""

import glob
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Default budgets (weighted tokens) per plan, calibrated against an observed
# Max (5x) account: session 16% used ≈ 750K, weekly 38% used ≈ 18M.
# Override via --session-limit / --weekly-limit or env vars.
PLAN_LIMITS = {
    "pro":   ( 1_000_000,  10_000_000),
    "max5":  ( 4_500_000,  48_000_000),
    "max20": (18_000_000, 192_000_000),
}


def find_jsonl(claude_dir: Path):
    return glob.glob(str(claude_dir / "projects" / "**" / "*.jsonl"), recursive=True)


def weighted_tokens(usage: dict) -> float:
    """Cost-weighted tokens (input-equivalent), using Anthropic pricing ratios.

    output 5×, cache_creation 1.25×, cache_read 0.1×.
    """
    return (
        usage.get("input_tokens", 0) * 1.0
        + usage.get("output_tokens", 0) * 5.0
        + usage.get("cache_creation_input_tokens", 0) * 1.25
        + usage.get("cache_read_input_tokens", 0) * 0.1
    )


def parse_records(paths):
    seen = set()
    for p in paths:
        try:
            f = open(p, "r", encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = d.get("message")
                ts = d.get("timestamp")
                if not isinstance(msg, dict) or not ts:
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                mid = msg.get("id")
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                yield dt, weighted_tokens(usage)


def session_window(records, now, hours=5):
    """Anthropic-style rolling 5h session.

    The session resets 5h after the oldest message in the rolling 5h window,
    so session_end = oldest_message_in_last_5h + 5h.
    """
    duration = timedelta(hours=hours)
    cutoff = now - duration
    in_window = sorted((t, n) for t, n in records if cutoff <= t <= now)
    if not in_window:
        return now, now + duration, []
    start = in_window[0][0]
    return start, start + duration, in_window


def weekly_window(records, now, reset_weekday=0, reset_hour=11, tz=None):
    """Fixed weekly window with a recurring reset at `reset_weekday`/`reset_hour`.

    `reset_weekday`: 0=Mon … 6=Sun. `reset_hour`: 0-23 in `tz` (default local).
    """
    if tz is None:
        local_now = now.astimezone()
    else:
        local_now = now.astimezone(tz)

    days_back = (local_now.weekday() - reset_weekday) % 7
    if days_back == 0 and (local_now.hour < reset_hour or
                           (local_now.hour == reset_hour and local_now.minute == 0
                            and local_now.second == 0)):
        days_back = 7
    last_reset_local = (local_now - timedelta(days=days_back)).replace(
        hour=reset_hour, minute=0, second=0, microsecond=0)
    next_reset_local = last_reset_local + timedelta(days=7)

    start = last_reset_local.astimezone(timezone.utc)
    end = next_reset_local.astimezone(timezone.utc)
    in_window = sorted((t, n) for t, n in records if start <= t <= now)
    return start, end, in_window


def cumulative_pct(records, start, budget):
    xs, ys = [], []
    total = 0.0
    for t, n in records:
        total += n
        xs.append((t - start).total_seconds() / 60.0)
        ys.append((total / budget * 100) if budget > 0 else 0)
    return xs, ys, total


def fmt_duration(td: timedelta) -> str:
    s = max(0, int(td.total_seconds()))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def fmt_count(n: float) -> str:
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:,.0f}"


def config_path() -> Path:
    return Path.home() / ".ccburn.json"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict) -> None:
    config_path().write_text(json.dumps(cfg, indent=2))


def calibrate(claude_dir: Path, plan: str, session_pct: float = None,
              weekly_pct: float = None, week_reset_day: int = 0,
              week_reset_hour: int = 0) -> dict:
    """Infer real budgets from the user's official Anthropic percentages.

    Reads current windowed totals, divides by the user-provided percentages,
    and persists the result to ~/.ccburn.json.
    """
    records = list(parse_records(find_jsonl(claude_dir)))
    now = datetime.now(timezone.utc)
    cfg = load_config()
    cfg.setdefault("plan", plan)

    if session_pct is not None and session_pct > 0:
        _, _, s_rec = session_window(records, now, hours=5)
        s_total = sum(n for _, n in s_rec)
        cfg["session_limit"] = int(s_total / (session_pct / 100))

    if weekly_pct is not None and weekly_pct > 0:
        _, _, w_rec = weekly_window(records, now, week_reset_day, week_reset_hour)
        w_total = sum(n for _, n in w_rec)
        cfg["weekly_limit"] = int(w_total / (weekly_pct / 100))

    save_config(cfg)
    return cfg
