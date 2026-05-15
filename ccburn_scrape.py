#!/usr/bin/env python3
"""ccburn_scrape - background scraper for Anthropic's account-wide usage.

Drives a headless Chrome (via Playwright) to claude.ai/settings/usage, parses
the displayed percentages, and pushes them into ~/.ccburn.json so the dashboard
shows your real usage without manual clicks.

First run requires a one-time login:
    python ccburn_scrape.py --login

Then just leave it running (ccburn.bat does this automatically):
    python ccburn_scrape.py
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.stderr.write(
        "Missing dependency. Install with:\n"
        "  python -m pip install playwright\n\n"
        "(uses your installed Chrome, no extra browser download needed)\n"
    )
    sys.exit(1)

from ccburn_lib import save_snapshot

PROFILE_DIR = Path.home() / ".ccburn-browser-profile"
USAGE_URL = "https://claude.ai/settings/usage"


def parse_usage_text(text: str) -> dict:
    text = text.replace("\xa0", " ").replace(" ", " ")
    s = re.search(r"Current session[\s\S]*?(\d+(?:\.\d+)?)\s*%", text, re.I)
    w = re.search(r"All models[\s\S]*?(\d+(?:\.\d+)?)\s*%", text, re.I)
    sr = re.search(
        r"Current session[\s\S]*?Resets?\s+in\s*(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?",
        text, re.I,
    )
    if not s or not w:
        return {}
    data = {"session_pct": float(s.group(1)), "weekly_pct": float(w.group(1))}
    if sr:
        data["session_reset_seconds"] = (
            int(sr.group(1) or 0) * 3600 + int(sr.group(2) or 0) * 60
        )
    return data


def _launch(headless: bool):
    p = sync_playwright().start()
    try:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 800},
            args=["--no-first-run", "--no-default-browser-check"],
        )
    except Exception:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 800},
        )
    return p, context


def scrape(headless: bool = True) -> dict:
    p, context = _launch(headless)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(USAGE_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector("text=/Current session/i", timeout=15000)
        except PWTimeout:
            body = page.inner_text("body")
            if re.search(r"sign\s*in|log\s*in", body, re.I):
                raise RuntimeError(
                    "Not logged in. Run once: python ccburn_scrape.py --login")
            raise RuntimeError(f"Usage page didn't render. First 200 chars: {body[:200]!r}")
        text = page.inner_text("body")
        data = parse_usage_text(text)
        if not data:
            raise RuntimeError(f"Couldn't parse usage. Snippet: {text[:300]!r}")
        return data
    finally:
        try:
            context.close()
        finally:
            p.stop()


def do_login():
    print("Opening Claude. Log in if asked, then close the browser window.")
    print(f"Profile will be saved at: {PROFILE_DIR}")
    p, context = _launch(headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(USAGE_URL)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
    finally:
        try:
            context.close()
        finally:
            p.stop()
    print("Login flow done. The scraper will run headless from now on.")


def main():
    ap = argparse.ArgumentParser(description="Auto-scrape Claude usage into ccburn")
    ap.add_argument("--login", action="store_true",
                    help="One-time: open Chrome for you to log in to Claude")
    ap.add_argument("--once", action="store_true", help="Scrape once and exit")
    ap.add_argument("--interval", type=int, default=600,
                    help="Seconds between scrapes (default 600 = 10 min)")
    ap.add_argument("--headed", action="store_true",
                    help="Show the browser window (for debugging)")
    args = ap.parse_args()

    if args.login:
        do_login()
        return

    while True:
        try:
            data = scrape(headless=not args.headed)
            save_snapshot(**data)
            stamp = datetime.now().strftime("%H:%M:%S")
            sess = data.get("session_pct")
            wk = data.get("weekly_pct")
            print(f"[{stamp}] auto-scraped: session={sess}%  weekly={wk}%")
        except KeyboardInterrupt:
            break
        except Exception as e:
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{stamp}] scrape failed: {e}", file=sys.stderr)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
