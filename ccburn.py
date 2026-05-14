#!/usr/bin/env python3
"""ccburn - terminal usage dashboard for Claude Code.

Use ccburn_web.py for the prettier browser version.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import plotext as plt
except ImportError:
    sys.stderr.write("Missing dependency: pip install plotext\n")
    sys.exit(1)

from ccburn_lib import (
    PLAN_LIMITS, calibrate, cumulative_pct, find_jsonl, fmt_count, fmt_duration,
    load_config, parse_records, session_window, weekly_window,
)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
COLOR = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "gray": "\033[38;5;244m", "green": "\033[38;5;46m",
    "orange": "\033[38;5;208m", "red": "\033[38;5;196m",
    "cyan": "\033[38;5;51m", "magenta": "\033[38;5;201m",
}


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def colored(s, c):
    return f"{COLOR.get(c, '')}{s}{COLOR['reset']}"


def pick_color(pct):
    return "green" if pct < 70 else "orange" if pct < 90 else "red"


def progress_bar(label, pct, width=30):
    pct_c = max(0.0, min(100.0, pct))
    filled = int(round(width * pct_c / 100))
    color = pick_color(pct_c) if label == "Usage" else "cyan"
    bar = colored("█" * filled, color) + colored("░" * (width - filled), "gray")
    return f"  {colored(label, 'dim'):>10} {bar} {pct:5.1f}%"


def header(title, reset_str, total_width=58):
    left = colored("🔥 ", "orange") + colored("ccburn", "bold") + colored(f"  {title}", "dim")
    right = colored(f"resets in {reset_str}", "cyan")
    pad = max(1, total_width - len(strip_ansi(left)) - len(strip_ansi(right)))
    return left + " " * pad + right


def render_panel(start, end, records, budget, now, width, height):
    plt.clear_figure()
    plt.plot_size(width, height)
    plt.theme("dark")
    total_min = (end - start).total_seconds() / 60.0
    elapsed_min = max(0.0, min(total_min, (now - start).total_seconds() / 60.0))
    plt.plot([0, total_min], [0, 100], marker="dot", color="gray", label="Pace")
    xs, ys, total = cumulative_pct(records, start, budget)
    ys = [min(y, 120) for y in ys]
    pct_now = (total / budget * 100) if budget > 0 else 0
    if xs:
        plt.plot(xs, ys, marker="braille", color=pick_color(pct_now), label="Usage")
    if elapsed_min > 0 and total > 0 and budget > 0:
        proj = total / elapsed_min * total_min / budget * 100
        plt.plot([0, total_min], [0, min(proj, 120)],
                 marker="dot", color="magenta", label="Projection")
    plt.plot([elapsed_min, elapsed_min], [0, 100],
             marker="braille", color="cyan", label="Now")
    plt.xlim(0, total_min); plt.ylim(0, 110)
    return plt.build()


def side_by_side(a, b, gap="   "):
    al, bl = a.split("\n"), b.split("\n")
    n = max(len(al), len(bl))
    al += [""] * (n - len(al)); bl += [""] * (n - len(bl))
    wa = max((len(strip_ansi(l)) for l in al), default=0)
    return "\n".join(la + " " * (wa - len(strip_ansi(la))) + gap + lb
                     for la, lb in zip(al, bl))


def render_once(args):
    claude_dir = Path(args.claude_dir).expanduser()
    if not claude_dir.exists():
        sys.stderr.write(f"No Claude data directory at {claude_dir}\n")
        sys.exit(1)

    records = list(parse_records(find_jsonl(claude_dir)))
    now = datetime.now(timezone.utc)
    s_start, s_end, s_rec = session_window(records, now, hours=5)
    w_start, w_end, w_rec = weekly_window(
        records, now, args.week_reset_day, args.week_reset_hour)

    s_total = sum(n for _, n in s_rec)
    w_total = sum(n for _, n in w_rec)
    s_pct = (s_total / args.session_limit * 100) if args.session_limit else 0
    w_pct = (w_total / args.weekly_limit * 100) if args.weekly_limit else 0
    s_elapsed = ((now - s_start).total_seconds() / (5 * 3600) * 100) if s_rec else 0
    w_elapsed = ((now - w_start).total_seconds() /
                 (w_end - w_start).total_seconds() * 100) if w_rec else 0

    panel_w = args.width
    s_block = (header("Session  (5h)", fmt_duration(s_end - now), panel_w) + "\n"
               + progress_bar("Usage", s_pct) + "\n"
               + progress_bar("Elapsed", s_elapsed) + "\n"
               + render_panel(s_start, s_end, s_rec, args.session_limit,
                              now, panel_w, args.height))
    w_block = (header("Weekly  (7d)", fmt_duration(w_end - now), panel_w) + "\n"
               + progress_bar("Usage", w_pct) + "\n"
               + progress_bar("Elapsed", w_elapsed) + "\n"
               + render_panel(w_start, w_end, w_rec, args.weekly_limit,
                              now, panel_w, args.height))

    if args.watch > 0:
        sys.stdout.write("\033[2J\033[H")
    print()
    print(side_by_side(s_block, w_block))
    print()
    print(
        f"  Plan: {colored(args.plan, 'bold')}    "
        f"Session: {colored(fmt_count(s_total), 'green')} / {fmt_count(args.session_limit)}    "
        f"Weekly: {colored(fmt_count(w_total), 'green')} / {fmt_count(args.weekly_limit)}    "
        f"{colored('(weighted tokens)', 'dim')}"
    )


def resolve_limits(args, cfg):
    default_s, default_w = PLAN_LIMITS[args.plan]
    if args.session_limit is None:
        args.session_limit = int(os.environ.get("CCBURN_SESSION_LIMIT",
                                                cfg.get("session_limit", default_s)))
    if args.weekly_limit is None:
        args.weekly_limit = int(os.environ.get("CCBURN_WEEKLY_LIMIT",
                                               cfg.get("weekly_limit", default_w)))


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Terminal usage dashboard for Claude Code")
    ap.add_argument("--claude-dir", default=os.environ.get(
        "CLAUDE_DIR", str(Path.home() / ".claude")))
    ap.add_argument("--plan", choices=PLAN_LIMITS.keys(),
                    default=os.environ.get("CCBURN_PLAN", cfg.get("plan", "max5")))
    ap.add_argument("--session-limit", type=int, default=None)
    ap.add_argument("--weekly-limit", type=int, default=None)
    ap.add_argument("--week-reset-day", type=int,
                    default=cfg.get("week_reset_day", 0),
                    help="0=Mon … 6=Sun (default 0)")
    ap.add_argument("--week-reset-hour", type=int,
                    default=cfg.get("week_reset_hour", 0),
                    help="0-23, local time (default 0)")
    ap.add_argument("--calibrate-session", type=float, metavar="PCT",
                    help="Current session %% from claude.ai; saves inferred limit")
    ap.add_argument("--calibrate-weekly", type=float, metavar="PCT",
                    help="Current weekly %% from claude.ai; saves inferred limit")
    ap.add_argument("--watch", "-w", type=float, default=0,
                    help="Refresh interval in seconds (0 = run once)")
    ap.add_argument("--width", type=int, default=58)
    ap.add_argument("--height", type=int, default=14)
    args = ap.parse_args()

    if args.calibrate_session is not None or args.calibrate_weekly is not None:
        new_cfg = calibrate(
            Path(args.claude_dir).expanduser(), args.plan,
            args.calibrate_session, args.calibrate_weekly,
            args.week_reset_day, args.week_reset_hour,
        )
        print("Calibrated and saved to ~/.ccburn.json:")
        for k, v in new_cfg.items():
            print(f"  {k}: {v}")
        return

    resolve_limits(args, cfg)

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
