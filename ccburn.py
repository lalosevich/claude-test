#!/usr/bin/env python3
"""ccburn - terminal usage dashboard for Claude Code.

Reads ~/.claude/projects/**/*.jsonl, sums cost-weighted tokens per assistant turn,
and renders two side-by-side panels: a 5-hour rolling session window and a 7-day window.
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import plotext as plt
except ImportError:
    sys.stderr.write("Missing dependency: pip install plotext\n")
    sys.exit(1)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Cost-weighted token budgets (input-equivalent). Anthropic doesn't publish real
# quotas, so these are calibrated guesses. Tune with --session-limit / --weekly-limit.
PLAN_LIMITS = {
    "pro":   (  5_000_000,  40_000_000),
    "max5":  ( 30_000_000, 250_000_000),
    "max20": (120_000_000, 1_000_000_000),
}


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def find_jsonl(claude_dir: Path):
    return glob.glob(str(claude_dir / "projects" / "**" / "*.jsonl"), recursive=True)


def weighted_tokens(usage: dict) -> float:
    """Cost-weighted tokens using Anthropic input-equivalent ratios.

    output ≈ 5× input, cache_creation ≈ 1.25×, cache_read ≈ 0.1×.
    Cache reads dominate raw counts but only cost ~10% of an input token,
    so weighting them avoids massive over-counting.
    """
    return (
        usage.get("input_tokens", 0) * 1.0
        + usage.get("output_tokens", 0) * 5.0
        + usage.get("cache_creation_input_tokens", 0) * 1.25
        + usage.get("cache_read_input_tokens", 0) * 0.1
    )


def parse_usage_records(paths):
    seen_ids = set()
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
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                yield dt, weighted_tokens(usage)


def session_window(records, now, hours=5):
    cutoff = now - timedelta(hours=hours)
    in_window = sorted((t, n) for t, n in records if cutoff <= t <= now)
    if not in_window:
        return now, now + timedelta(hours=hours), []
    start = in_window[0][0]
    return start, start + timedelta(hours=hours), in_window


def weekly_window(records, now, days=7):
    cutoff = now - timedelta(days=days)
    in_window = sorted((t, n) for t, n in records if cutoff <= t <= now)
    if not in_window:
        return now, now + timedelta(days=days), []
    start = in_window[0][0]
    return start, start + timedelta(days=days), in_window


def cumulative_pct(records, start, budget):
    xs, ys = [], []
    total = 0.0
    for t, n in records:
        total += n
        xs.append((t - start).total_seconds() / 60.0)
        ys.append(min((total / budget) * 100, 120) if budget > 0 else 0)
    return xs, ys, total


def fmt_duration(td: timedelta) -> str:
    s = max(0, int(td.total_seconds()))
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_count(n: float) -> str:
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:,.0f}"


def pick_usage_color(pct: float) -> str:
    if pct < 70:
        return "green"
    if pct < 90:
        return "orange"
    return "red"


def render_panel(start, end, records, budget, now, width, height):
    plt.clear_figure()
    plt.plot_size(width, height)
    plt.theme("dark")

    total_min = (end - start).total_seconds() / 60.0
    elapsed_min = max(0.0, min(total_min, (now - start).total_seconds() / 60.0))

    plt.plot([0, total_min], [0, 100], marker="dot", color="gray", label="Pace")

    xs, ys, total = cumulative_pct(records, start, budget)
    pct_now = (total / budget) * 100 if budget > 0 else 0
    usage_color = pick_usage_color(pct_now)
    if xs:
        plt.plot(xs, ys, marker="braille", color=usage_color, label="Usage")

    if elapsed_min > 0 and total > 0 and budget > 0:
        rate = total / elapsed_min
        proj_pct = (rate * total_min / budget) * 100
        plt.plot([0, total_min], [0, min(proj_pct, 120)],
                 marker="dot", color="magenta", label="Projection")

    plt.plot([elapsed_min, elapsed_min], [0, 100],
             marker="braille", color="cyan", label="Now")

    plt.xlim(0, total_min)
    plt.ylim(0, 110)
    return plt.build()


COLOR = {
    "reset": "\033[0m",
    "dim":   "\033[2m",
    "bold":  "\033[1m",
    "gray":  "\033[38;5;244m",
    "green": "\033[38;5;46m",
    "orange":"\033[38;5;208m",
    "red":   "\033[38;5;196m",
    "cyan":  "\033[38;5;51m",
    "magenta":"\033[38;5;201m",
}


def colored(s: str, c: str) -> str:
    return f"{COLOR.get(c, '')}{s}{COLOR['reset']}"


def progress_bar(label, pct, width=30):
    pct_clamped = max(0.0, min(100.0, pct))
    filled = int(round(width * pct_clamped / 100))
    color = pick_usage_color(pct_clamped) if label == "Usage" else "cyan"
    bar = colored("█" * filled, color) + colored("░" * (width - filled), "gray")
    return f"  {colored(label, 'dim'):>10} {bar} {pct:5.1f}%"


def header(title, reset_str, pct_total_width=58):
    left = colored("🔥 ", "orange") + colored("ccburn", "bold") + colored(f"  {title}", "dim")
    right = colored(f"resets in {reset_str}", "cyan")
    pad = max(1, pct_total_width - len(strip_ansi(left)) - len(strip_ansi(right)))
    return left + " " * pad + right


def side_by_side(a: str, b: str, gap: str = "   ") -> str:
    a_lines, b_lines = a.split("\n"), b.split("\n")
    n = max(len(a_lines), len(b_lines))
    a_lines += [""] * (n - len(a_lines))
    b_lines += [""] * (n - len(b_lines))
    width_a = max((len(strip_ansi(l)) for l in a_lines), default=0)
    return "\n".join(
        la + " " * (width_a - len(strip_ansi(la))) + gap + lb
        for la, lb in zip(a_lines, b_lines)
    )


def render_once(args):
    claude_dir = Path(args.claude_dir).expanduser()
    if not claude_dir.exists():
        sys.stderr.write(f"No Claude data directory at {claude_dir}\n")
        sys.exit(1)

    records = list(parse_usage_records(find_jsonl(claude_dir)))
    now = datetime.now(timezone.utc)

    s_start, s_end, s_rec = session_window(records, now, hours=5)
    w_start, w_end, w_rec = weekly_window(records, now, days=7)

    s_total = sum(n for _, n in s_rec)
    w_total = sum(n for _, n in w_rec)
    s_pct = (s_total / args.session_limit) * 100 if args.session_limit else 0
    w_pct = (w_total / args.weekly_limit) * 100 if args.weekly_limit else 0
    s_elapsed_pct = ((now - s_start).total_seconds() / (5 * 3600)) * 100 if s_rec else 0
    w_elapsed_pct = ((now - w_start).total_seconds() / (7 * 86400)) * 100 if w_rec else 0

    panel_w = args.width

    s_block = (
        header("Session  (5h)", fmt_duration(s_end - now), panel_w) + "\n" +
        progress_bar("Usage", s_pct) + "\n" +
        progress_bar("Elapsed", s_elapsed_pct) + "\n" +
        render_panel(s_start, s_end, s_rec, args.session_limit, now, panel_w, args.height)
    )
    w_block = (
        header("Weekly  (7d)", fmt_duration(w_end - now), panel_w) + "\n" +
        progress_bar("Usage", w_pct) + "\n" +
        progress_bar("Elapsed", w_elapsed_pct) + "\n" +
        render_panel(w_start, w_end, w_rec, args.weekly_limit, now, panel_w, args.height)
    )

    if args.watch > 0:
        sys.stdout.write("\033[2J\033[H")
    print()
    print(side_by_side(s_block, w_block))
    print()
    footer = (
        f"  Plan: {colored(args.plan, 'bold')}    "
        f"Session: {colored(fmt_count(s_total), 'green')} / {fmt_count(args.session_limit)}    "
        f"Weekly: {colored(fmt_count(w_total), 'green')} / {fmt_count(args.weekly_limit)}    "
        f"{colored('(weighted tokens)', 'dim')}"
    )
    print(footer)


def main():
    ap = argparse.ArgumentParser(description="Terminal usage dashboard for Claude Code")
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    ap.add_argument("--plan", choices=PLAN_LIMITS.keys(),
                    default=os.environ.get("CCBURN_PLAN", "max5"),
                    help="Plan preset for budgets (default: max5; env CCBURN_PLAN)")
    ap.add_argument("--session-limit", type=int, default=None,
                    help="Override session weighted-token budget")
    ap.add_argument("--weekly-limit", type=int, default=None,
                    help="Override weekly weighted-token budget")
    ap.add_argument("--watch", "-w", type=float, default=0,
                    help="Refresh interval in seconds (0 = run once)")
    ap.add_argument("--width", type=int, default=58, help="Panel width in columns")
    ap.add_argument("--height", type=int, default=14, help="Panel height in rows")
    args = ap.parse_args()

    default_s, default_w = PLAN_LIMITS[args.plan]
    if args.session_limit is None:
        args.session_limit = int(os.environ.get("CCBURN_SESSION_LIMIT", default_s))
    if args.weekly_limit is None:
        args.weekly_limit = int(os.environ.get("CCBURN_WEEKLY_LIMIT", default_w))

    if args.watch > 0:
        try:
            while True:
                render_once(args)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print()
    else:
        render_once(args)


if __name__ == "__main__":
    main()
