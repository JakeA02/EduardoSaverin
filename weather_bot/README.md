# Trading Bot (Polymarket + Kalshi)

Integrated from [polymarket-kalshi-weather-bot](https://github.com/suislanchez/polymarket-kalshi-weather-bot). Runs as a **separate tab** at `/bot/` and does not share code with `natgas/` or `kalshi_wx/`.

## Setup

Kalshi credentials are read from the **repo root** `.env` and `rsa.txt` (same as the rest of EduardoSaverin):

- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY` (optional inline PEM) or `rsa.txt` at repo root

## Run locally

```bash
# Terminal 1 — FastAPI backend on :8000
./weather_bot/serve.sh

# Terminal 2 — React dev server on :3000 (proxies /api → :8000)
cd weather_bot/frontend && npm install && npm run dev
```

Or open the static build at `/bot/` after running `./weather_bot/build-dashboard.sh` (requires backend on :8000).

## Deploy backend to Fly.io (free tier)

The bot needs an **always-on** process (APScheduler). Fly.io’s free allowance fits a single 512 MB machine with a 1 GB volume for SQLite.

### One-time setup (needs your Fly account)

```bash
# Install Fly CLI
brew install flyctl
# or: curl -L https://fly.io/install.sh | sh

# Log in (opens browser)
fly auth login
```

If `eduardo-weather-bot` is taken, edit `app = "..."` in `weather_bot/fly.toml` before deploying.

### Deploy

From repo root:

```bash
./weather_bot/fly-deploy.sh --rebuild-ui
```

This will:

1. Create the Fly app (if new)
2. Create a 1 GB volume for `tradingbot.db` (persists across redeploys)
3. Push Kalshi secrets from repo-root `.env` / `rsa.txt`
4. Deploy the Docker container
5. Rebuild `dashboard/bot/` with `VITE_API_URL` set to your Fly URL

Then commit and push `dashboard/bot/` so Vercel serves the UI against Fly.

### Manual secret setup

If you prefer not to auto-import from `.env`:

```bash
fly secrets set \
  KALSHI_API_KEY_ID="your-key-id" \
  KALSHI_PRIVATE_KEY="$(cat rsa.txt)" \
  -a eduardo-weather-bot
```

### Useful commands

```bash
fly logs -a eduardo-weather-bot      # tail logs
fly status -a eduardo-weather-bot    # machine + health
fly ssh console -a eduardo-weather-bot
curl https://eduardo-weather-bot.fly.dev/api/health
```

Redeploy after code changes:

```bash
./weather_bot/fly-deploy.sh
```

## Build for Vercel (frontend only)

```bash
# Local backend
./weather_bot/build-dashboard.sh

# Fly backend
VITE_API_URL=https://eduardo-weather-bot.fly.dev ./weather_bot/build-dashboard.sh
```

Commit `dashboard/bot/`. The tab appears in site nav alongside Storage and Weather markets.
