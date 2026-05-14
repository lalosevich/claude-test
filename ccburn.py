#!/usr/bin/env python3
"""ccburn - terminal usage dashboard for Claude Code.

Reads ~/.claude/projects/**/*.jsonl, sums tokens per assistant turn, and renders
two side-by-side panels: a 5-hour rolling session window and a 7-day window.
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


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def find_jsonl(claude_dir: Path):
    return glob.glob(str(claude_dir / "projects" / "**" / "*.jsonl"), recursive=True)


def parse_usage_records(paths):
    """Yield (datetime_utc, total_tokens) for each unique assistant turn."""
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
                tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
                yield dt, tokens


def session_window(records, now, hours=5):
    """Anchor session start at the first record in the last `hours`."""
    cutoff = now - timedelta(hours=hours)
    in_window = sorted((t, n) for t, n in records if cutoff <= t <= now)
    if not in_window:
        return now, now + timedelta(hours=hours), []
    start = in_window[0][0]
    return start, start + timedelta(hours=hours), in_window


def weekly_window(records, now, days=7):
    """Anchor weekly start at the first record in the last `days`."""
    cutoff = now - timedelta(days=days)
    in_window = sorted((t, n) for t, n in records if cutoff <= t <= now)
    if not in_window:
        return now, now + timedelta(days=days), []
    start = in_window[0][0]
    return start, start + timedelta(days=days), in_window


def cumulative_pct(records, start, budget):
    xs, ys = [], []
    total = 0
    for t, n in records:
        total += n
        xs.append((t - start).total_seconds() / 60.0)
        pct = (total / budget) * 100 if budget > 0 else 0
        ys.append(min(pct, 120))
    return xs, ys, total


def fmt_duration(td: timedelta) -> str:
    s = max(0, int(td.total_seconds()))
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def render_panel(title, start, end, records, budget, now, width, height):
    plt.clear_figure()
    plt.plot_size(width, height)
    plt.title(title)
    plt.theme("dark")

    total_min = (end - start).total_seconds() / 60.0
    elapsed_min = max(0.0, min(total_min, (now - start).total_seconds() / 60.0))

    plt.plot([0, total_min], [0, 100], marker="dot", color="white", label="Budget Pace")

    xs, ys, total = cumulative_pct(records, start, budget)
    pct_now = (total / budget) * 100 if budget > 0 else 0
    color = "green+" if pct_now < 70 else "yellow+" if pct_now < 100 else "red+"
    if xs:
        plt.plot(xs, ys, marker="braille", color=color, label="Usage")

    if elapsed_min > 0 and total > 0 and budget > 0:
        rate = total / elapsed_min
        proj_pct = (rate * total_min / budget) * 100
        plt.plot([0, total_min], [0, min(proj_pct, 120)],
                 marker="dot", color="magenta", label="Projection")

    plt.plot([elapsed_min, elapsed_min], [0, 100],
             marker="braille", color="blue", label="Now")

    plt.xlim(0, total_min)
    plt.ylim(0, 110)
    return plt.build()


def progress_bar(label, pct, width=28):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100))
    return f"{label:>8} " + "█" * filled + "░" * (width - filled) + f" {pct:5.1f}%"


def side_by_side(a: str, b: str, gap: str = "  │  ") -> str:
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

    s_title = f"ccburn — Session (5h)   resets in {fmt_duration(s_end - now)}"
    w_title = f"ccburn — Weekly         resets in {fmt_duration(w_end - now)}"

    s_plot = render_panel(s_title, s_start, s_end, s_rec, args.session_limit, now, args.width, args.height)
    w_plot = render_panel(w_title, w_start, w_end, w_rec, args.weekly_limit, now, args.width, args.height)

    header_l = progress_bar("Usage", s_pct) + "\n" + progress_bar("Elapsed", s_elapsed_pct)
    header_r = progress_bar("Usage", w_pct) + "\n" + progress_bar("Elapsed", w_elapsed_pct)

    if args.watch > 0:
        sys.stdout.write("\033[2J\033[H")
    print(side_by_side(header_l, header_r))
    print(side_by_side(s_plot, w_plot))
    print(
        f"\nSession: {s_total:>12,} / {args.session_limit:>12,} tokens   "
        f"|   Weekly: {w_total:>12,} / {args.weekly_limit:>12,} tokens"
    )


def main():
    ap = argparse.ArgumentParser(description="Terminal usage dashboard for Claude Code")
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    ap.add_argument("--session-limit", type=int,
                    default=int(os.environ.get("CCBURN_SESSION_LIMIT", 7_000_000)),
                    help="Session token budget (default 7M; env CCBURN_SESSION_LIMIT)")
    ap.add_argument("--weekly-limit", type=int,
                    default=int(os.environ.get("CCBURN_WEEKLY_LIMIT", 70_000_000)),
                    help="Weekly token budget (default 70M; env CCBURN_WEEKLY_LIMIT)")
    ap.add_argument("--watch", "-w", type=float, default=0,
                    help="Refresh interval in seconds (0 = run once)")
    ap.add_argument("--width", type=int, default=58, help="Panel width in columns")
    ap.add_argument("--height", type=int, default=16, help="Panel height in rows")
    args = ap.parse_args()

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
