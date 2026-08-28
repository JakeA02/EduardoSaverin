# Weather-Driven Natural Gas Storage Forecasting

**An implementation guide, from empty repo to live capital**

---

## Scope and premise

You are building a system that predicts the **weekly change in US natural gas working storage** before the EIA publishes it, using numerical weather prediction output as the primary input. The prediction is then converted into a directional or volatility signal on natural gas futures.

The reason this is the right first project: the target is published weekly, a public analyst consensus exists to benchmark against, the causal chain from weather to the target is short and physical, and you can validate the entire modeling stack for months without risking a dollar.

**The thing you are forecasting is not price.** It is a physical quantity with a known accounting identity. Price enters only in the final phase, and only as a function of your error versus the market's error.

### Success criterion, defined up front

Before writing code, commit to this: **you continue only if your out-of-sample storage forecast beats the Reuters/NGI analyst consensus median absolute error over a rolling 26-week window.** If it doesn't, you have a fun data pipeline and no edge. Write that number on the wall. The most common failure mode in this space is a model that backtests beautifully and never gets checked against the actual competition.

---

## Phase 0 — Environment and repository

**Goal:** reproducible pipeline, versioned data, no notebook archaeology.

### 0.1 Stack

```
Python 3.11+
uv or poetry for dependency management
DuckDB for the analytical store (single file, zero admin, reads Parquet natively)
Parquet on disk for all raw pulls, partitioned by source and date
Polars or pandas for transforms
scikit-learn / statsmodels for Phase 2, xgboost or lightgbm later
herbie-data for GRIB2 retrieval
xarray + cfgrib for grid handling
```

Skip Postgres. Skip Airflow. DuckDB plus GitHub Actions cron covers this workload entirely and costs nothing.

### 0.2 Repository layout

```
natgas/
├── ingest/           # one module per data source, each idempotent
│   ├── eia.py
│   ├── cpc.py
│   ├── ghcn.py
│   ├── nwp.py        # GFS/GEFS/ECMWF grid retrieval
│   └── consensus.py
├── features/
│   ├── degree_days.py
│   ├── weights.py    # population & gas-heating weights
│   └── balance.py    # supply/demand identity terms
├── models/
│   ├── baseline.py
│   ├── ensemble.py
│   └── evaluate.py
├── signal/
│   ├── translate.py  # storage surprise -> trade signal
│   └── risk.py
├── data/
│   ├── raw/          # immutable, append-only
│   ├── interim/
│   └── warehouse.duckdb
├── logs/predictions/ # append-only, timestamped, never edited
└── tests/
```

### 0.3 The one rule that matters

**Every prediction gets written to an append-only log with a UTC timestamp at the moment it is made, before the outcome is known.** No exceptions, no edits, no "I would have caught that." This log is the only honest record you will have. Store it as line-delimited JSON in git so the commit history itself timestamps it.

```python
# logs/predictions/2026-08-28T14:32:11Z.json
{
  "ts_utc": "2026-08-28T14:32:11Z",
  "target_week_ending": "2026-08-28",
  "model_version": "baseline-v0.3",
  "nwp_cycle": "2026-08-28T12:00Z",
  "nwp_source": "gefs",
  "predicted_change_bcf": -58.2,
  "prediction_interval_90": [-71.0, -45.4],
  "consensus_median": null,
  "actual": null
}
```

Backfill `consensus_median` and `actual` in a separate process. Never overwrite `predicted_change_bcf`.

---

## Phase 1 — Historical data foundation

**Goal:** ten-plus years of aligned weekly history. No modeling yet.

### 1.1 The target: EIA Weekly Natural Gas Storage Report

The WNGSR is released **Thursdays at 10:30am ET** and covers the storage week ending the **previous Friday**. Internalize that offset — it is the single most common source of look-ahead bias in this project.

- **API:** EIA API v2, free key from the EIA site.
- **Route:** `/v2/natural-gas/stor/wkly/data/`
- **Series:** total lower-48 working gas, plus the five regions (East, Midwest, Mountain, Pacific, South Central), and South Central split into **salt** and **nonsalt**.

Model the total first. The regional breakdown matters later because South Central salt behaves differently — salt domes cycle fast and respond to price arbitrage as much as weather, which adds noise to a purely weather-driven model.

**Revisions.** EIA occasionally reclassifies or revises. If you build features from the current-vintage series, your backtest sees corrections that weren't available at the time. Snapshot each weekly pull as its own Parquet file rather than maintaining one mutable table, so you can reconstruct what was knowable on any past date.

### 1.2 The primary feature: degree days

```
HDD = max(0, 65°F − T_mean_daily)
CDD = max(0, T_mean_daily − 65°F)
```

Where `T_mean = (Tmax + Tmin) / 2`. The 65°F base is convention, not physics. Treat it as a hyperparameter later — the optimal base for gas demand often lands slightly lower than 65 for heating and slightly higher for cooling.

**Ground truth source:** NOAA CPC publishes population-weighted daily and weekly degree days by state and census division. This is what the market watches, so it is your reference series even where you compute your own.

**Station-level source:** NOAA GHCN-Daily via NCEI, for building custom weightings. Also NCEI's hourly ISD if you eventually want subdaily resolution.

### 1.3 Building gas-weighted degree days (GWDD)

This is where you start to differ from the crowd, so do it carefully.

CPC's degree days are **population-weighted**. Gas demand is not proportional to population — it's proportional to population × heating fuel share × per-capita consumption intensity. New England burns more heating oil; the Southeast heats largely with electricity (which shifts demand into power burn rather than ResComm); the Upper Midwest is heavily gas.

Build your weight vector as:

```
w_region ∝ population_region × gas_heating_share_region × intensity_region
```

- Population: Census county/state data.
- Gas heating share: Census American Community Survey, "house heating fuel" table.
- Intensity: calibrate empirically — regress historical ResComm consumption on regional HDD and let the coefficients tell you the weights.

The empirical calibration usually beats the demographic construction. Do both and compare.

### 1.4 The rest of the balance

```
Δstorage = production + net_imports − ResComm − power_burn
           − industrial − LNG_feedgas − exports_MX
```

All available from EIA, though at varying frequency and lag:

| Term | Source | Frequency | Lag |
|---|---|---|---|
| Dry gas production | EIA monthly + pipeline nominations for daily proxy | Monthly / daily proxy | Weeks |
| LNG feedgas | Pipeline scrapes; EIA monthly | Daily proxy | Days |
| Power burn | EIA-930 hourly electric grid monitor | Hourly | ~1 day |
| Industrial | EIA monthly | Monthly | ~2 months |
| Exports to Mexico | EIA monthly | Monthly | ~2 months |
| Canadian imports | EIA monthly | Monthly | ~2 months |

The slow-moving terms are your problem. Industrial demand at two-month lag is useless as a real-time input, so model it as a seasonal baseline plus trend. Production is the term most likely to break your model — a production surge or a freeze-off can swamp the weather signal entirely.

**EIA-930 is underused and worth your attention.** Hourly generation by fuel type across all balancing authorities, one-day lag, free API. It gives you a near-real-time read on gas burn for power, which is the fastest-moving weather-sensitive term in the identity.

### 1.5 Phase 1 exit criteria

- A DuckDB table `weekly_panel` keyed on `week_ending`, with ≥10 years of storage change, CPC HDD/CDD, your GWDD, and the balance terms.
- A vintage-aware snapshot directory so you can reconstruct historical knowability.
- Unit tests confirming your week-alignment logic against a handful of hand-checked EIA releases.

---

## Phase 2 — Baseline model on observed weather

**Goal:** establish how much of storage change weather explains, using *observed* weather. No forecasting yet. This isolates the demand model from the meteorology.

### 2.1 Start embarrassingly simple

```python
Δstorage ~ β₀ + β₁·GWHDD + β₂·GWCDD + seasonal_dummies + trend
```

OLS. Look at R². In winter you should get quite far; shoulder seasons are much worse. If this doesn't explain a large majority of winter variance, your degree-day construction or week alignment is wrong. Fix it before proceeding.

### 2.2 Add structure, in this order

1. **Nonlinearity in cooling.** Power burn is convex in temperature — an extra degree at 95°F drives far more gas than an extra degree at 78°F. Splines on CDD, or a piecewise-linear term with a knot around 80°F.
2. **Nonlinearity in heating.** Less pronounced but present at extreme cold, where heating saturates and freeze-offs cut *supply* simultaneously. Extreme cold can be storage-bullish through both channels at once.
3. **Regional decomposition.** Fit per-region and sum. Regional models usually beat the national aggregate because the demand response differs by region.
4. **Production and LNG.** Add as levels or as deviations from a trailing baseline.
5. **Wind and solar generation.** From EIA-930. High wind displaces gas in ERCOT and SPP — this is a real and growing term that pure degree-day models miss.
6. **Day-of-week and holiday effects.** Industrial demand drops over Thanksgiving and Christmas.

### 2.3 Validation discipline

Time-series cross-validation only. `sklearn.model_selection.TimeSeriesSplit`, expanding window. **Never** shuffle. Never use k-fold. You have roughly 520 weekly observations for a decade — this is a small-data problem, which is exactly why you should be suspicious of anything more complex than gradient boosting on a dozen features.

Report **MAE in Bcf**, not R². The market talks in Bcf and the consensus is scored in Bcf.

### 2.4 Phase 2 exit criteria

- Observed-weather model with winter MAE that you can articulate against a naive seasonal-average baseline.
- A written note on which balance terms actually moved the needle. Most won't.

---

## Phase 3 — The forecast pipeline

**Goal:** replace observed weather with forecast weather. This is where the real engineering lives.

### 3.1 Model sources, all free

| Model | Access | Resolution | Runs | Notes |
|---|---|---|---|---|
| **GFS** | AWS `noaa-gfs-bdp-pds`, no credentials | 0.25° | 00/06/12/18z | Deterministic, 384h |
| **GEFS** | AWS `noaa-gefs-pds` | 0.5° | 4×/day | 31 members — this is your uncertainty source |
| **ECMWF IFS/AIFS** | ECMWF Open Data | 0.25° | 00/06/12/18z | Historically the strongest deterministic model; open data is a subset of the full product |
| **CFS / CPC 6-10 & 8-14 day** | NOAA | Coarse | Daily | Subseasonal outlooks |

Use **`herbie`** (the `herbie-data` package) for retrieval. It handles GRIB2 subsetting from cloud storage via byte-range requests, so you pull only the `TMP:2 m above ground` field rather than the full multi-gigabyte file. This is the difference between a two-minute job and an unusable one.

```python
from herbie import Herbie

H = Herbie("2026-08-28 12:00", model="gfs", product="pgrb2.0p25", fxx=24)
ds = H.xarray("TMP:2 m above ground")
```

### 3.2 Grid to degree days

1. Retrieve 2m temperature on the native grid for all forecast hours.
2. Resample to daily Tmax/Tmin per grid cell.
3. Compute HDD/CDD per cell.
4. Apply your Phase 1 weight raster (regrid population/gas-heating weights onto the NWP grid once, cache it).
5. Aggregate to regional and national GWDD by forecast day.
6. Sum across the target EIA storage week.

Cache aggressively. The weight raster never changes. Grid-to-region mappings never change. Only the temperature fields do.

### 3.3 Bias correction

Raw NWP 2m temperature has systematic bias — by model, by region, by season, by lead time. You **must** correct this or your degree days will be systematically off.

Build a bias table: for each (model, region, lead_time, month), compute mean error of forecast degree days versus CPC-observed degree days over the past several years. Subtract it. Refresh quarterly.

This step alone is often worth more than any model architecture choice.

### 3.4 Ensemble handling

Don't just use the GEFS mean. Compute the full distribution:

- Run each of the 31 members through your demand model → 31 storage predictions.
- The **spread** is your uncertainty estimate, and it is itself tradeable information: wide spread at 8–14 days is a signal about implied volatility, independent of direction.
- Track spread-versus-realized-error calibration. If your 90% intervals contain the truth 60% of the time, your intervals are wrong and you'll size positions badly later.

### 3.5 Run cadence

Nail the timing to the EIA calendar. The storage week ends Friday; the report drops the following Thursday. So the forecast horizon shrinks each day:

- **Saturday–Friday:** pure forecast, 1–7 days out.
- **Following Sat–Wed:** the week is *over*; you're now waiting on observed data. Your model should switch to observed weather and converge sharply.
- **Thursday 10:30 ET:** truth.

That convergence pattern is diagnostic. Plot your prediction path for each week. If it doesn't tighten as forecast is replaced by observation, something is wrong.

### 3.6 Automation

GitHub Actions on cron, four times daily, aligned ~90 minutes after each model cycle initialization to allow for run availability. Commit predictions to the repo. Free, versioned, and the commit timestamp is a third-party attestation of when you made the call.

If you need the 06z run reliably at 3am ET and Actions' scheduling jitter bothers you, a $5/month VPS is the only money this project needs. Your $100 covers most of a year.

---

## Phase 4 — Benchmarking against consensus

**Goal:** find out whether you have anything. This phase is a pass/fail gate.

### 4.1 Collect the consensus

Reuters and Natural Gas Intelligence both survey analysts and publish a median estimate and range, typically Tuesday–Wednesday before Thursday's release. Also useful: the Wall Street Journal survey and Bloomberg's consensus where accessible.

Log it in your prediction records **without ever letting it influence your prediction.** Capture your number first, then the consensus, in separate writes.

### 4.2 The metrics that matter

| Metric | Definition | Why |
|---|---|---|
| **MAE vs. consensus MAE** | Rolling 26-week | The headline gate |
| **Directional agreement on surprise** | Did you correctly call whether actual would land above or below consensus? | This is what actually trades |
| **Hit rate on large surprises** | Restricted to weeks where \|actual − consensus\| > 5 Bcf | Big surprises are where the money is |
| **Interval calibration** | Coverage of your 90% intervals | Determines position sizing |

Directional agreement on surprise is the real number. You do not profit from being close to the actual; you profit from being closer than the market on the side that matters.

### 4.3 Honest gate

Run this for a **full winter minimum**. One shoulder season proves nothing. If after 26 weeks your MAE is not below consensus MAE, and your surprise-direction hit rate is not meaningfully above 50%, the correct action is to stop, or to go back to Phase 2 and find which balance term is failing you.

This is a real possibility, not a formality. The market's consensus is the aggregate of desks doing exactly this with better data and full-time meteorologists. Being merely competitive with it is already a good outcome for a solo project.

---

## Phase 5 — Signal construction and paper trading

**Goal:** turn a storage forecast into a defined trade, and test the translation layer.

### 5.1 The translation problem

A correct storage forecast is not automatically a profitable trade. You need an empirical mapping:

```
expected_price_move = f(predicted_surprise, storage_context, positioning, season)
```

Estimate `f` by regressing historical front-month settlement change on release day against the historical surprise (actual − consensus). Expect a noisy relationship with meaningful heteroskedasticity — the same 10 Bcf surprise moves price far more in January than in April, and far more when storage is already tight relative to the five-year average.

Include interaction terms for:
- Storage level versus five-year average (tightness amplifies surprise sensitivity)
- Season / month
- Realized volatility regime
- Days to contract expiry

### 5.2 Trade expression

You have three broad choices, in increasing sophistication:

1. **Directional front-month futures.** Simplest. Highest variance. Fully exposed to everything unrelated to your model.
2. **Calendar spreads.** Long one month, short another. Removes much of the parallel-shift risk and isolates the storage/weather view. Lower margin. Generally the more intelligent expression of a storage view, and where a lot of the professional activity sits.
3. **Options.** If your genuine edge turns out to be *ensemble spread* rather than direction — which is a real possibility — then volatility is the correct instrument, not delta.

Be honest with yourself about which of the three your model actually supports. A model that predicts storage well but has no directional edge on the surprise might still have a volatility edge.

### 5.3 Instruments

| Ticker | Contract | Notional per $1 move | Comment |
|---|---|---|---|
| **NG** | Henry Hub, 10,000 MMBtu | $10,000 | Full-size. Too large to learn on. |
| **QG** | E-mini, 2,500 MMBtu | $2,500 | The reasonable retail instrument |
| **UNG** | ETF | — | Roll decay in contango; a poor long-term holding but usable for short-horizon expression |
| **BOIL / KOLD** | 2× leveraged ETFs | — | Avoid. Daily-rebalance decay will overwhelm any signal you have. |

Natural gas is one of the most volatile futures markets that exists. Multi-standard-deviation days are routine, and winter gap risk is severe — the market can open far from where it closed on a weekend model shift, straight through any stop you've placed.

### 5.4 Paper trading setup

Interactive Brokers offers a standalone paper account with live futures data. Use it for realistic fills, margin mechanics, and the discipline of an actual order ticket.

But **also** maintain a separate CSV-based paper log with settlement-price fills, because it forces you to specify entry, exit, and sizing rules precisely enough to be executed by code. If you can't write the rule down, you don't have a strategy — you have a feeling.

Track, per trade: signal value, position size and its justification, entry, exit, P&L, and a one-line note on whether the outcome validated the *reasoning* or just happened to be profitable. Those diverge more often than is comfortable.

### 5.5 Duration

**Minimum one full heating season of paper trading before any real capital.** October through March. There is no shortcut here, and the impulse to shortcut it is exactly the thing that costs people money.

---

## Phase 6 — Live capital

**Goal:** trade small, survive, and find out what breaks when money is involved.

### 6.1 Regulatory position

Trading your own account requires no license or registration. Registration obligations arise when you cross into managing others' money, advising for compensation, or operating a pool. Solo, own-account, self-directed trading sits outside that.

You do need an account at a registered futures commission merchant. IBKR, TradeStation, AMP, and NinjaTrader all serve retail futures.

### 6.2 Sizing

Start at a size where **total account loss would be genuinely unimportant to you.** Not "uncomfortable" — unimportant. This is the number one determinant of whether you make good decisions in the first six months.

Size positions off your *calibrated* uncertainty, not off conviction:

```
position_size ∝ edge / variance
```

A fractional Kelly (quarter-Kelly or less) is a reasonable starting frame, but Kelly assumes you know your edge. You don't — you have an estimate from 26 weeks. Discount it hard.

### 6.3 Hard operational rules, written before you trade

- **Maximum loss per week**, defined in dollars, enforced by code.
- **Maximum consecutive losing weeks** before mandatory stand-down and model review.
- **No discretionary override of the model.** If you override, log it as a separate strategy and track it separately. You will discover whether your discretion helps. Usually it doesn't.
- **No trading during illiquid hours.** Gas can be thin overnight, and slippage will eat a small edge.
- **Freeze-off and hurricane protocol.** Extreme events break the model's assumptions in both supply and demand simultaneously. Decide in advance whether you flatten or hold, and write it down now, not during the event.

### 6.4 Tax note

US futures generally fall under Section 1256, with mark-to-market treatment and a 60/40 long-term/short-term split regardless of holding period. This differs meaningfully from equity or ETF treatment, and it applies whether or not you closed the position by year end. Talk to an accountant before your first December — I'm not a tax professional and the details depend on your situation.

---

## Phase 7 — Operations and extension

### 7.1 Monitoring

- **Drift alarms.** Rolling MAE versus your backtest MAE. When live error exceeds backtest error by a set margin for a set duration, stand down and investigate.
- **Feature drift.** Production growth, LNG buildout, and the wind/solar share of generation all shift the balance structurally. A model trained on 2015–2020 embeds a fundamentally different LNG export picture than today's.
- **Annual refit** with expanding window, not sliding — you don't have enough data to discard old observations casually.

### 7.2 Where the extensions are

Once the core works, in rough order of value:

1. **Regional models and basis.** Weather is regional; Henry Hub is one point. Basis spreads between regional hubs are more directly weather-driven than the national benchmark and are less crowded.
2. **Power burn as a primary target.** EIA-930 gives you hourly gas generation with a one-day lag. Forecasting it directly is a faster-cycling problem with far more data points than 52 storage prints a year.
3. **Weather derivatives.** CME lists HDD/CDD futures and options directly. Thin and awkward for retail, but it is the purest expression of a weather view — no demand model required, which also means no demand-model edge.
4. **Subseasonal.** Weeks 3–4 forecasting is where deterministic skill collapses and where a genuinely better probabilistic approach could differentiate. Hard, and the reason it's unsolved is that it's hard.
5. **ML weather models.** GraphCast, and ECMWF's AIFS, are openly available and competitive with traditional NWP at a fraction of the compute. Running your own ensemble perturbations through an ML model is now feasible on modest hardware — this is the most interesting frontier available to an individual right now.

---

## Realistic expectations

The pipeline is a genuinely satisfying engineering project and you will learn a great deal building it regardless of outcome. But be clear-eyed about the competitive landscape: the desks on the other side of your trades employ full-time PhD meteorologists, license proprietary high-resolution model output, run their own ensemble perturbations, and have pipeline-level scrape data you can't easily replicate.

Your plausible edges are narrow but real: **systematic discipline** where others are discretionary, **speed of reaction** to model cycles, **a better demand transfer function** than the crowd, and **patience to trade only when your signal is strong** — which nobody paid a salary to trade has the luxury of.

Expect the first winter to be a research season, not a profitable one. The Phase 4 gate exists to tell you the truth. Respect the answer it gives you, including if that answer is no.

None of this is financial advice — it's a research and engineering plan. Natural gas is among the most volatile markets available to retail traders, and leveraged futures positions can lose more than the capital committed to them.