"""Command-line interface."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from natgas.config import ensure_dirs

app = typer.Typer(help="Weather-driven natural gas storage forecasting")
console = Console()


@app.callback()
def main() -> None:
    ensure_dirs()


ingest_app = typer.Typer(help="Data ingestion")
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("eia")
def ingest_eia_cmd(start: str = "2010-01-01") -> None:
    from natgas.ingest.eia import ingest

    df = ingest(start=start)
    latest = df.dropna(subset=["storage_change_bcf"]).tail(1)
    extra = ""
    if not latest.empty:
        row = latest.iloc[0]
        extra = f" · last {row['week_ending']} {row['storage_change_bcf']:+.1f} Bcf"
    console.print(
        f"[green]EIA storage:[/green] {len(df)} weeks -> "
        f"{df['week_ending'].min()} .. {df['week_ending'].max()}{extra}"
    )


@ingest_app.command("cpc")
def ingest_cpc_cmd(start_year: int = 2010) -> None:
    from natgas.ingest.cpc import ingest

    df = ingest(start_year=start_year)
    console.print(f"[green]CPC degree days:[/green] {len(df)} rows")


@ingest_app.command("cpc-forecast")
def ingest_cpc_forecast_cmd(start_year: int = 2020) -> None:
    from natgas.ingest.cpc_forecast import ingest

    df = ingest(start_year=start_year)
    console.print(f"[green]CPC 7-day forecasts:[/green] {len(df)} rows, {df['issued'].nunique()} issue dates")


@ingest_app.command("gfs")
def ingest_gfs_cmd(
    cycle: str = typer.Option(None, help="Cycle UTC, YYYY-MM-DDTHH (default: latest 00/06/12/18)"),
) -> None:
    from datetime import datetime, timezone

    from natgas.ingest.nwp import fetch_cycle_division_dd

    if cycle:
        when = datetime.fromisoformat(cycle).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        hour = (now.hour // 6) * 6
        when = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    df = fetch_cycle_division_dd(when.replace(tzinfo=None))
    out = Path("data/interim/gfs_latest.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    console.print(f"[green]GFS division DD:[/green] {len(df)} rows -> {out}")


@ingest_app.command("consensus")
def ingest_consensus_cmd() -> None:
    """Fetch public analyst survey estimates (Trading Economics; Investing.com if reachable)."""
    from natgas.ingest.consensus import ingest

    df = ingest()
    if df.empty:
        console.print("[yellow]No consensus rows yet.[/yellow] Check network access to Trading Economics.")
        raise typer.Exit(1)
    last = df.iloc[-1]
    console.print(
        f"[green]Consensus:[/green] {len(df)} weeks "
        f"({df['week_ending'].min()} .. {df['week_ending'].max()}) "
        f"latest {last['consensus_median']:.1f} Bcf [{last['source']}]"
    )


@ingest_app.command("balance")
def ingest_balance_cmd() -> None:
    from natgas.ingest.balance import ingest_balance_terms, save_balance_snapshot

    df = ingest_balance_terms()
    path = save_balance_snapshot(df)
    console.print(f"[green]Balance terms saved:[/green] {path}")


@app.command("build-panel")
def build_panel_cmd(refresh: bool = typer.Option(False, help="Re-fetch all raw data")) -> None:
    from natgas.features.balance import build_panel

    panel = build_panel(refresh=refresh)
    console.print(f"[green]weekly_panel:[/green] {len(panel)} rows in DuckDB")


@app.command("train")
def train_cmd(
    output: Path = typer.Option(Path("data/interim/model.joblib"), help="Model output path"),
    model: str = typer.Option("ols", help="Model type: ols or lgbm"),
) -> None:
    import joblib

    from natgas.features.balance import load_panel
    from natgas.models.baseline import fit_baseline_ols
    from natgas.models.ensemble import fit_lightgbm

    panel = load_panel()
    if model == "lgbm":
        fitted = fit_lightgbm(panel)
    else:
        fitted = fit_baseline_ols(panel)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(fitted, output)
    console.print(f"[green]Trained[/green] {fitted.name} on {len(panel)} weeks -> {output}")


@app.command("backtest")
def backtest_cmd(
    n_splits: int = 5,
    model: str = typer.Option("ols", help="Model type: ols or lgbm"),
    output: Path = typer.Option(Path("data/interim/backtest.parquet"), help="Predictions output"),
) -> None:
    from natgas.features.balance import load_panel
    from natgas.models.evaluate import run_backtest

    panel = load_panel()
    result = run_backtest(panel, n_splits=n_splits, model_type=model)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.predictions.to_parquet(output, index=False)

    table = Table(title="Backtest Results (MAE in Bcf)")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Model MAE", f"{result.model_mae:.2f}")
    table.add_row("Naive seasonal MAE", f"{result.naive_mae:.2f}")
    table.add_row("Bias (pred − actual)", f"{result.bias:+.2f}")
    table.add_row("Winter model MAE", f"{result.winter_model_mae:.2f}" if result.winter_model_mae else "n/a")
    table.add_row("Fold MAEs", ", ".join(f"{m:.2f}" for m in result.fold_mae))
    console.print(table)
    console.print(f"Predictions saved to {output}")


@app.command("forecast-backtest")
def forecast_backtest_cmd(
    as_of: str = typer.Option("saturday", help="saturday (week-start) or wednesday (mid-week)"),
    model: str = "ols",
    output: Path = Path("data/interim/forecast_backtest.parquet"),
    observed_path: Path = Path("data/interim/backtest.parquet"),
) -> None:
    from natgas.features.balance import load_panel
    from natgas.ingest.cpc import load_latest_daily
    from natgas.ingest.cpc_forecast import load_latest_vintage
    from natgas.models.forecast import run_forecast_backtest

    panel = load_panel()
    observed = load_latest_daily()
    forecasts = load_latest_vintage()
    result = run_forecast_backtest(panel, observed, forecasts, as_of=as_of, model_type=model)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.predictions.to_parquet(output, index=False)

    obs_mae = None
    if observed_path.exists():
        obs = pd.read_parquet(observed_path)
        obs["week_ending"] = pd.to_datetime(obs["week_ending"]).dt.date
        fcst = result.predictions.copy()
        fcst["week_ending"] = pd.to_datetime(fcst["week_ending"]).dt.date
        both = obs.merge(fcst, on="week_ending", suffixes=("_obs", "_fcst"))
        if not both.empty:
            obs_mae = float((both["predicted_obs"] - both["storage_change_bcf_obs"]).abs().mean())

    table = Table(title=f"Phase 3 Forecast-Weather Backtest (as-of {as_of})")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Forecast-weather MAE", f"{result.model_mae:.2f}")
    if obs_mae is not None:
        table.add_row("Observed-weather MAE (same weeks)", f"{obs_mae:.2f}")
        table.add_row("Weather-error penalty", f"{result.model_mae - obs_mae:+.2f}")
    table.add_row("Naive seasonal MAE", f"{result.naive_mae:.2f}")
    table.add_row("Bias (pred − actual)", f"{result.bias:+.2f}")
    table.add_row("Winter MAE", f"{result.winter_model_mae:.2f}" if result.winter_model_mae else "n/a")
    table.add_row("Weeks scored", str(len(result.predictions)))
    table.add_row("Fold MAEs", ", ".join(f"{m:.2f}" for m in result.fold_mae))
    console.print(table)
    console.print(f"Predictions saved to {output}")


@ingest_app.command("weathernext")
def ingest_weathernext_cmd(
    hours: int = typer.Option(240, help="Hourly horizon to request (Maps API max 240)"),
) -> None:
    """Maps Platform Weather API → division HDD/CDD (WeatherNext 3)."""
    from natgas.ingest.weathernext import WeatherNextConfigError, ingest

    try:
        df = ingest(hours=hours)
    except WeatherNextConfigError as exc:
        console.print(f"[yellow]WeatherNext skipped:[/yellow] {exc}")
        raise typer.Exit(1)
    console.print(
        f"[green]WeatherNext 3:[/green] {len(df)} rows, "
        f"{df['date'].nunique()} days, {df['issued'].nunique()} issue date(s)"
    )


@app.command("predict")
def predict_cmd(
    week_ending: str = typer.Option(
        None, help="Single target Friday (YYYY-MM-DD); default: current EIA week (and next if --weeks 2)"
    ),
    model_path: Path = Path("data/interim/model.joblib"),
    source: str = typer.Option(
        "cpc-forecast",
        help="observed, cpc-forecast, weathernext, or all (CPC production + WeatherNext A/B)",
    ),
    weeks: int | None = typer.Option(
        None,
        help="How many storage weeks to score. Default 1 for CPC, 2 for weathernext/all.",
    ),
) -> None:
    import joblib

    from natgas.features.balance import load_panel
    from natgas.ingest.weathernext import WeatherNextConfigError
    from natgas.models.live import default_weeks_for_source, run_live

    panel = load_panel()
    panel["week_ending"] = pd.to_datetime(panel["week_ending"]).dt.date
    if not model_path.exists():
        console.print(f"[red]No model at {model_path}. Run `natgas train` first.[/red]")
        raise typer.Exit(1)
    model = joblib.load(model_path)
    sources = ["cpc-forecast", "weathernext"] if source == "all" else [source]
    as_of = datetime.now(UTC).date()
    targets = None
    if week_ending:
        first = date.fromisoformat(week_ending)
        n = weeks if weeks else 1
        targets = [first + timedelta(days=7 * i) for i in range(n)]

    any_ok = False
    for src in sources:
        n_weeks = default_weeks_for_source(src, weeks) if targets is None else len(targets)
        try:
            results = run_live(
                source=src,
                weeks=n_weeks,
                model=model,
                panel=panel,
                as_of=as_of,
                targets=targets,
            )
        except WeatherNextConfigError as exc:
            console.print(f"[yellow]WeatherNext skipped:[/yellow] {exc}")
            continue
        except Exception as exc:
            if src == "cpc-forecast":
                console.print(f"[yellow]Forecast blend failed ({exc}); trying observed weather.[/yellow]")
                results = run_live(
                    source="observed",
                    weeks=1,
                    model=model,
                    panel=panel,
                    as_of=as_of,
                    targets=targets[:1] if targets else None,
                )
            else:
                console.print(f"[yellow]{src} failed:[/yellow] {exc}")
                continue
        for item in results:
            if not item.complete:
                console.print(
                    f"[yellow]Skip h{item.horizon_weeks} week ending {item.target_week_ending}:[/yellow] "
                    f"{item.skipped_reason}"
                )
                continue
            any_ok = True
            console.print(
                f"[green]Prediction:[/green] {item.predicted_change_bcf:.1f} Bcf "
                f"(90% [{item.interval_lower:.1f}, {item.interval_upper:.1f}]) "
                f"week ending {item.target_week_ending} "
                f"[h{item.horizon_weeks} {item.nwp_source}] -> {item.path}"
            )
    if not any_ok:
        console.print("[red]No complete-week predictions written.[/red]")
        raise typer.Exit(1)


@app.command("init-db")
def init_db_cmd() -> None:
    from natgas.db import init_schema

    init_schema()
    console.print("[green]DuckDB schema initialized.[/green]")


@app.command("consensus")
def consensus_cmd(
    week_ending: str = typer.Argument(..., help="Target Friday YYYY-MM-DD"),
    median: float = typer.Argument(..., help="Consensus median Bcf change"),
    low: float | None = typer.Option(None),
    high: float | None = typer.Option(None),
    source: str = "manual",
) -> None:
    """Log analyst consensus AFTER the model prediction. Never feeds the model."""
    from natgas.ingest.consensus import append_consensus

    path = append_consensus(week_ending, median, low=low, high=high, source=source)
    console.print(f"[green]Consensus logged[/green] {median:.1f} Bcf for {week_ending} -> {path}")


@app.command("benchmark")
def benchmark_cmd(
    backtest_path: Path = Path("data/interim/forecast_backtest.parquet"),
) -> None:
    from natgas.ingest.consensus import load_consensus
    from natgas.models.consensus_eval import evaluate_vs_consensus

    path = backtest_path
    if not path.exists():
        path = Path("data/interim/backtest.parquet")
    if not path.exists():
        console.print("[red]No backtest file. Run natgas forecast-backtest or natgas backtest.[/red]")
        raise typer.Exit(1)

    pred_df = pd.read_parquet(path)
    consensus = load_consensus()
    if consensus.empty:
        console.print(
            "[yellow]Phase 4 gate needs analyst consensus.[/yellow] "
            "Add rows via `natgas consensus WEEK MEDIAN` or data/raw/consensus/consensus.csv"
        )
        raise typer.Exit(1)

    report = evaluate_vs_consensus(pred_df, consensus)
    table = Table(title="Phase 4 Gate vs Analyst Consensus")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Overlapping weeks", str(report.n_weeks))
    table.add_row("Model MAE", f"{report.model_mae:.2f} Bcf")
    table.add_row("Consensus MAE", f"{report.consensus_mae:.2f} Bcf")
    table.add_row(
        "Rolling 26w model MAE",
        f"{report.rolling_26_model_mae:.2f}" if report.rolling_26_model_mae is not None else "n/a",
    )
    table.add_row(
        "Rolling 26w consensus MAE",
        f"{report.rolling_26_consensus_mae:.2f}" if report.rolling_26_consensus_mae is not None else "n/a",
    )
    table.add_row("Surprise direction hit rate", f"{report.surprise_hit_rate:.1%}")
    table.add_row(
        f"Large-surprise hit (|Δ|>5 Bcf, n={report.large_surprise_n})",
        f"{report.large_surprise_hit_rate:.1%}" if report.large_surprise_hit_rate is not None else "n/a",
    )
    table.add_row(
        "90% interval coverage",
        f"{report.interval_coverage_90:.1%}" if report.interval_coverage_90 is not None else "n/a",
    )
    table.add_row("Gate", "PASS" if report.gate_pass else ("FAIL" if report.gate_pass is False else "INCOMPLETE"))
    console.print(table)
    console.print(report.gate_reason)


@app.command("paper")
def paper_cmd(
    hindcast: bool = typer.Option(
        False,
        help="Also settle historical weeks using the OLS backtest where no live log exists",
    ),
) -> None:
    """Open/settle the QG paper ledger from the latest prediction + consensus."""
    from natgas.signal.paper import paper_summary, sync_paper

    ledger = sync_paper(hindcast=hindcast)
    summary = paper_summary(ledger)
    table = Table(title="Paper ledger (QG, settlement fills)")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Settled weeks", str(summary["n_settled"]))
    table.add_row("Open", str(summary["n_open"]))
    table.add_row("Skipped", str(summary["n_skipped"]))
    table.add_row("Cumulative P&L", f"${summary['cumulative_pnl_usd']:,.0f}")
    table.add_row(
        "Win rate",
        f"{summary['win_rate']:.0%}" if summary["win_rate"] is not None else "n/a",
    )
    table.add_row(
        "Reasoning hit",
        f"{summary['reasoning_hit_rate']:.0%}" if summary["reasoning_hit_rate"] is not None else "n/a",
    )
    table.add_row("Consecutive losses", str(summary["consecutive_losses"]))
    if summary["latest_open"]:
        open_ = summary["latest_open"]
        table.add_row(
            "Working order",
            f"{open_['side']} {open_['n_contracts']} QG · {open_['week_ending']} · "
            f"surprise {open_['predicted_surprise']:+.1f}",
        )
    console.print(table)


@app.command("dashboard")
def dashboard_cmd(output: Path = Path("dashboard")) -> None:
    """Write a static dashboard (predictions vs consensus vs EIA actuals)."""
    from natgas.dashboard import generate

    path = generate(output)
    console.print(f"[green]Dashboard:[/green] {path}")


if __name__ == "__main__":
    app()
