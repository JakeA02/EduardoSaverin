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
| Consensus | Automated from [Trading Economics](https://tradingeconomics.com/united-states/natural-gas-stocks-change) via `natgas ingest consensus` | For Phase 4 gate |

CPC degree days and NOAA GFS/GEFS (via Herbie) require no API keys.

The pipeline runs unattended on **GitHub Actions** ([Forecast Pipeline](https://github.com/JakeA02/EduardoSaverin/actions)). Each run ingests EIA + weather + consensus, writes a prediction, refreshes `dashboard/`, and deploys it.

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
| `natgas dashboard` | Write static dashboard to `dashboard/` |
| `natgas benchmark` | Compare backtest vs analyst consensus (Phase 4 gate) |
| `natgas predict` | Log a prediction for the target week |

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

Your model must beat consensus rolling 26-week MAE before moving to paper trading (Phase 5).

## Tests

```bash
uv run pytest
```
