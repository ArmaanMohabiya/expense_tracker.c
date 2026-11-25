@echo off
rem One-step start for Windows: double-click this file.
rem Creates a virtual environment the first time, installs Flask, then starts the app.
cd /d "%~dp0"
if not exist venv (
    echo First run: setting up ^(needs internet once^)...
    python -m venv venv || goto :error
    venv\Scripts\pip install -r requirements.txt || goto :error
)
if not exist instance\expense_tracker.db (
    venv\Scripts\flask --app app seed-demo
)
echo Open http://127.0.0.1:5000 in your browser ^(Ctrl+C to stop^)
venv\Scripts\flask --app app run
goto :eof
:error
echo.
echo Setup failed. Check that Python 3.9 or newer is installed and that you are online.
pause
