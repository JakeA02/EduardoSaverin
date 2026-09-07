#!/usr/bin/env bash
# Deploy weather_bot backend to Fly.io (free tier, always-on scheduler).
#
# Prerequisites (one-time, on your machine):
#   brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
#   fly auth login
#
# Usage:
#   ./weather_bot/fly-deploy.sh              # first deploy + secrets from repo root
#   ./weather_bot/fly-deploy.sh --rebuild-ui   # also rebuild dashboard/bot/ for Vercel
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOT_DIR="$REPO_ROOT/weather_bot"
REBUILD_UI=false

for arg in "$@"; do
  case "$arg" in
    --rebuild-ui) REBUILD_UI=true ;;
    -h|--help)
      echo "Usage: ./weather_bot/fly-deploy.sh [--rebuild-ui]"
      exit 0
      ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

if ! command -v fly >/dev/null 2>&1; then
  echo "Fly CLI not found. Install:"
  echo "  brew install flyctl"
  echo "  # or: curl -L https://fly.io/install.sh | sh"
  exit 1
fi

if ! fly auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Fly. Run: fly auth login"
  exit 1
fi

cd "$BOT_DIR"

APP_NAME="$(awk -F'"' '/^app = / { print $2; exit }' fly.toml)"
REGION="$(awk -F'"' '/^primary_region = / { print $2; exit }' fly.toml)"
REGION="${REGION:-ewr}"

if [[ -z "$APP_NAME" ]]; then
  echo "Could not read app name from fly.toml (expected: app = \"your-app-name\")"
  exit 1
fi

# Register app on first run (keeps fly.toml app name)
if ! fly status -a "$APP_NAME" >/dev/null 2>&1; then
  echo "Creating Fly app '$APP_NAME' in region $REGION..."
  fly apps create "$APP_NAME" --org personal 2>/dev/null || fly apps create "$APP_NAME"
fi

# Persistent SQLite volume (1 GB — within free allowance)
if ! fly volumes list -a "$APP_NAME" 2>/dev/null | grep -q tradingbot_data; then
  echo "Creating volume tradingbot_data (1 GB) in $REGION..."
  fly volumes create tradingbot_data --size 1 --region "$REGION" -a "$APP_NAME" --yes
fi

# Secrets from repo root .env + rsa.txt
ENV_FILE="$REPO_ROOT/.env"
RSA_FILE="$REPO_ROOT/rsa.txt"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

if [[ -n "${KALSHI_API_KEY_ID:-}" ]]; then
  echo "Setting KALSHI_API_KEY_ID..."
  fly secrets set "KALSHI_API_KEY_ID=$KALSHI_API_KEY_ID" -a "$APP_NAME"
fi

if [[ -n "${KALSHI_PRIVATE_KEY:-}" ]]; then
  echo "Setting KALSHI_PRIVATE_KEY (from .env)..."
  fly secrets set "KALSHI_PRIVATE_KEY=$KALSHI_PRIVATE_KEY" -a "$APP_NAME"
elif [[ -f "$RSA_FILE" ]]; then
  echo "Setting KALSHI_PRIVATE_KEY (from rsa.txt)..."
  fly secrets set "KALSHI_PRIVATE_KEY=$(cat "$RSA_FILE")" -a "$APP_NAME"
fi

if [[ -n "${KALSHI_PROD_DATA_BASE_URL:-}" ]]; then
  fly secrets set "KALSHI_PROD_DATA_BASE_URL=$KALSHI_PROD_DATA_BASE_URL" -a "$APP_NAME"
fi

if [[ -z "${KALSHI_API_KEY_ID:-}" ]] && [[ ! -f "$RSA_FILE" ]] && [[ -z "${KALSHI_PRIVATE_KEY:-}" ]]; then
  echo "Warning: no Kalshi credentials found in .env or rsa.txt — set secrets manually:"
  echo "  fly secrets set KALSHI_API_KEY_ID=... KALSHI_PRIVATE_KEY=\"\$(cat rsa.txt)\" -a $APP_NAME"
fi

echo "Deploying to Fly..."
fly deploy -a "$APP_NAME"

HOSTNAME="$(fly info -a "$APP_NAME" 2>/dev/null | awk '/Hostname/ {print $2}')"
API_URL="https://${HOSTNAME:-$APP_NAME.fly.dev}"

echo ""
echo "Deployed: $API_URL"
echo "Health:   $API_URL/api/health"
echo "Docs:     $API_URL/docs"
echo ""

if [[ "$REBUILD_UI" == true ]]; then
  echo "Rebuilding dashboard/bot/ with VITE_API_URL=$API_URL ..."
  VITE_API_URL="$API_URL" "$BOT_DIR/build-dashboard.sh"
  echo "Commit dashboard/bot/ and push for Vercel to pick up the API URL."
else
  echo "Rebuild the Vercel frontend tab to point at Fly:"
  echo "  VITE_API_URL=$API_URL ./weather_bot/build-dashboard.sh"
  echo "  git add dashboard/bot/ && git commit -m 'Point trading bot UI at Fly backend'"
fi
