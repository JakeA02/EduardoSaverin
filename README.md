# Weather-Driven Natural Gas Storage Forecasting

Pipeline for predicting weekly US natural gas storage changes from weather and balance data.

## Quick start

```bash
# 1. Install
uv sync --dev

# 2. Add your free EIA API key
cp .env.example .env
# Edit .env and set EIA_API_KEY=...

# 3. Initialize DB and ingest data
uv run natgas init-db
uv run natgas ingest eia
uv run natgas ingest cpc
uv run natgas ingest balance   # optional, needs EIA key

# 4. Build panel, train, backtest
uv run natgas build-panel --refresh
uv run natgas train                  # OLS baseline
uv run natgas train --model lgbm     # LightGBM (usually better)
uv run natgas backtest
uv run natgas backtest --model lgbm
```

## What you need

| Key | Source | Required |
|-----|--------|----------|
| `EIA_API_KEY` | [EIA Open Data](https://www.eia.gov/opendata/register.php) | Yes |
| `GOOGLE_MAPS_API_KEY` | [Maps Platform Weather API](https://developers.google.com/maps/documentation/weather/overview) | For WeatherNext 3 A/B (optional) |
| Consensus | Automated from [Trading Economics](https://tradingeconomics.com/united-states/natural-gas-stocks-change) via `natgas ingest consensus` | For Phase 4 gate |

CPC degree days and NOAA GFS/GEFS (via Herbie) require no API keys.

The pipeline runs unattended on **GitHub Actions** ([Forecast Pipeline](https://github.com/JakeA02/EduardoSaverin/actions)). Each run ingests EIA + weather + consensus, writes a prediction, refreshes `dashboard/` (storage at `/` and Kalshi weather at `/kalshi/`), and deploys it.

Add `GOOGLE_MAPS_API_KEY` as a repo secret to enable the WeatherNext 3 A/B (and next-week attempt). Without it, CPC-NDFD still runs.

**Live dashboard:** https://eduardo-saverin-rouge.vercel.app  
**Kalshi weather:** https://eduardo-saverin-rouge.vercel.app/kalshi/  
**Trading bot:** https://eduardo-saverin-rouge.vercel.app/bot/ (backend on [Fly.io](weather_bot/README.md#deploy-backend-to-flyio-free-tier))

## Project layout

```
natgas/           Python package (EIA storage)
kalshi_wx/        Python package (Kalshi weather vs WeatherNext 3)
kalshi/           TypeScript Kalshi client (demo orders; not on the live path yet)
weather_bot/      Polymarket + Kalshi trading bot (separate FastAPI + React app)
dashboard/        Nat-gas Vercel root
dashboard/kalshi/ Kalshi weather dashboard (served at /kalshi/)
dashboard/bot/    Trading bot React dashboard (served at /bot/)
data/raw/         Versioned Parquet snapshots (gitignored)
data/interim/     Models, backtest outputs
logs/predictions/ Nat-gas JSON prediction log
logs/kalshi/      Kalshi weather scan log
tests/            Unit tests
```

## Commands

| Command | Description |
|---------|-------------|
| `natgas ingest eia` | Pull weekly storage from EIA API v2 |
| `natgas ingest cpc` | Pull CPC population-weighted degree days |
| `natgas build-panel` | Merge sources into DuckDB `weekly_panel` |
| `natgas train` | Fit baseline OLS on full history |
| `natgas backtest` | Time-series CV; reports MAE in Bcf |
| `natgas backtest --model lgbm` | Same, with gradient boosting |
| `natgas ingest consensus` | Pull Trading Economics survey estimates (Investing.com if reachable) |
| `natgas paper` | Open/settle QG paper trades from prediction vs consensus |
| `natgas dashboard` | Write static dashboard to `dashboard/` |
| `natgas benchmark` | Compare backtest vs analyst consensus (Phase 4 gate) |
| `natgas ingest weathernext` | Maps Weather API → division HDD/CDD (WeatherNext 3) |
| `natgas predict` | Log a prediction for the current EIA week (CPC NDFD) |
| `natgas predict --source all` | CPC production + WeatherNext 3 A/B; WN3 also tries next week |
| `kalshi-wx scan` | WeatherNext 3 at Kalshi stations vs open HIGH/RAIN books; paper ledger |
| `kalshi-wx dashboard` | Write `dashboard/kalshi/` (Vercel `/kalshi/`) |

## Phase 4 gate

Analyst consensus is ingested automatically from the Trading Economics economist survey into `data/consensus/consensus.csv` (git-tracked, never a model feature):

```bash
uv run natgas ingest consensus
uv run natgas dashboard
```

Manual override still works and is never overwritten by the scraper:

```bash
uv run natgas consensus 2026-08-22 -58 --low -65 --high -50 --source manual
```

Your model must beat consensus rolling 26-week MAE before **live** capital (Phase 6). Paper trading starts now so the ledger exists before that gate.

## WeatherNext 3 A/B and week+1

`natgas predict --source all` logs the production CPC-NDFD number and a parallel WeatherNext 3 number (`nwp_source: weathernext3`). Paper trades keep using CPC until the A/B wins.

WeatherNext 3 synoptic cycles go 15 days, which is enough for **two** Saturday–Friday storage weeks from Saturday. The Maps Weather API path used here is capped at **10 days (240 hours)**, so next week is a complete 7-day blend only from about Wednesday onward. Incomplete weeks are skipped, not guessed.

```bash
uv run natgas ingest weathernext
uv run natgas predict --source weathernext --weeks 2
```

## Paper trading

Hypothetical **QG** (E-mini NG) trades from predicted surprise vs consensus. Rules live in `natgas/signal/translate.py` and are executed by `natgas paper` on GitHub Actions. The ledger is `data/paper/trades.csv`.

- Long QG if the model expects a *tighter* print than the street (predicted − consensus ≤ −5 Bcf)
- Short QG if it expects a *fatter* injection (≥ +5 Bcf)
- 1 contract in the 5–15 Bcf band, 2 contracts above 15, otherwise flat
Fills use EIA Henry Hub spot (RNGWHHD) as a proxy for NG settlement; the official print can lag 1–2 sessions, so a working order may stay open over a weekend.
- This is **not** a live IBKR account — settlement-price P&L only

```bash
uv run natgas paper --hindcast
uv run natgas dashboard
```

## Kalshi weather (daily / weekly)

Separate from storage. Maps WeatherNext 3 hourly Tmax / QPF at named CLI stations is scored against open Kalshi weather contracts. The Kalshi mid is the consensus. Two independent paper books share the converter and do not overlap — compare them after ~14 days:

| Book | Horizon | Markets | Volume floor |
| --- | --- | --- | --- |
| `near` | calendar D+1 | daily HIGH / RAIN | 50 |
| `far` | D+2–10 | HIGH / RAIN plus weekend rain and weekly heat | 10 |

Both still require a two-sided book between 5¢ and 95¢ and EV ≥ 5¢ after the quadratic taker fee. Maps Weather API tops out at 240 hours, so the far book cannot see the full 15-day WeatherNext synoptic. Existing `wx-kalshi-v0.1` ledger rows count as `near`. Nothing here writes a live Kalshi order.

```bash
uv run kalshi-wx scan
uv run kalshi-wx dashboard
```

The static app is `dashboard/kalshi/` so Vercel keeps storage at `/` and weather at `/kalshi/`. GitHub Actions runs the scan on the same forecast cron.

## Tests

```bash
uv run pytest
```
