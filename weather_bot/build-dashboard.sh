#!/usr/bin/env bash
# Build React dashboard → dashboard/bot/ for Vercel /bot/ tab.
# Set VITE_API_URL for production (e.g. Fly backend):
#   VITE_API_URL=https://eduardo-weather-bot.fly.dev ./weather_bot/build-dashboard.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/weather_bot/frontend"

npm ci
VITE_BASE_PATH=/bot/ VITE_OUT_DIR="$ROOT/dashboard/bot" npm run build

echo "Built → $ROOT/dashboard/bot/"
