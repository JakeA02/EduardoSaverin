#!/usr/bin/env bash
# Run the trading bot FastAPI backend (uses repo-root .env + rsa.txt).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/weather_bot"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

export PYTHONPATH="$ROOT/weather_bot:${PYTHONPATH:-}"
exec .venv/bin/python run.py
