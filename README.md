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

The pipeline runs unattended on **GitHub Actions** ([Forecast Pipeline](https://github.com/JakeA02/EduardoSaverin/actions)). Each run ingests EIA + weather + consensus, writes a prediction, refreshes `dashboard/`, and deploys it.

Add `GOOGLE_MAPS_API_KEY` as a repo secret to enable the WeatherNext 3 A/B (and next-week attempt). Without it, CPC-NDFD still runs.

**Live dashboard:** https://eduardo-saverin-rouge.vercel.app

## Project layout

```
natgas/           Python package
data/raw/         Versioned Parquet snapshots (gitignored)
data/interim/     Models, backtest outputs
logs/predictions/ Append-only JSON prediction log
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

## Tests

```bash
uv run pytest
```
