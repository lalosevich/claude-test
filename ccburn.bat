@echo off
title ccburn - Claude Code usage
cd /d "%~dp0"

echo.
echo  ============================================
echo    ccburn - dashboard with auto-scraper
echo  ============================================
echo.

if not exist "%USERPROFILE%\.ccburn-browser-profile" (
    echo  First-time setup: a Chrome window will open at claude.ai
    echo  Log in if asked, then CLOSE the window to continue.
    echo.
    python ccburn_scrape.py --login
    echo.
    echo  Login profile saved. Starting dashboard...
    echo.
)

echo  Starting dashboard - your browser will open automatically.
echo  Auto-scraper will run silently in the background.
echo  Press Ctrl+C in this window to stop everything.
echo.

python ccburn_web.py --auto-scrape

echo.
echo  ccburn stopped. Press any key to close.
pause >nul
