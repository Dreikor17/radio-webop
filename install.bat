@echo off
REM ============================================================
REM  Radio WebOp - one-click install / update (Windows)
REM  Double-click this file. It checks for Python (installs it
REM  if needed), then sets up everything and makes a desktop
REM  shortcut. Safe to run again anytime to UPDATE.
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
