@echo off
REM ============================================================
REM  Radio WebOp launcher (Windows)
REM  Uses the local .venv created by install.bat. If that is
REM  missing, it falls back to system Python. Closing this
REM  window stops the server.
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
    goto :done
)

echo No local environment found - trying system Python.
echo (Tip: double-click install.bat first for a clean one-click setup.)
echo.
where py >nul 2>nul && (py run.py %*) || (python run.py %*)

:done
REM keep the window open if the server exited with an error
if errorlevel 1 pause
