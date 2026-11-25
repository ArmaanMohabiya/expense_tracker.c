#!/bin/bash
# One-step start for macOS / Linux:  bash run.sh
# Creates a virtual environment the first time, installs Flask, then starts the app.
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
if [ ! -d venv ]; then
    echo "First run: setting up (needs internet once)..."
    $PY -m venv venv || exit 1
    venv/bin/pip install -r requirements.txt || exit 1
fi
if [ ! -f instance/expense_tracker.db ]; then
    venv/bin/flask --app app seed-demo      # demo account: demo / demo12345
fi
echo "Open http://127.0.0.1:5000 in your browser (Ctrl+C to stop)"
venv/bin/flask --app app run
