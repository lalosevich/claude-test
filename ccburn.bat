@echo off
title ccburn - Claude Code usage
cd /d "%~dp0"

echo.
echo  ============================================
echo    ccburn - dashboard
echo  ============================================
echo.
echo  Starting... your browser will open automatically.
echo  Press Ctrl+C in this window to stop.
echo.

python ccburn_web.py

echo.
echo  ccburn stopped. Press any key to close.
pause >nul
