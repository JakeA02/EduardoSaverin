"""Command-line interface for Kalshi weather paper trading."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from kalshi_wx.config import FORECAST_HOURS, ensure_dirs

app = typer.Typer(help="Kalshi weather markets vs WeatherNext 3 (paper)")
console = Console()


@app.callback()
def _main() -> None:
    ensure_dirs()


@app.command("scan")
def scan_cmd(
    hours: int = typer.Option(FORECAST_HOURS, help="Maps hourly horizon (max 240)"),
    skip_paper: bool = typer.Option(False, help="Do not open/settle the paper ledger"),
) -> None:
    """Pull Kalshi books + WeatherNext station forecasts, log gaps, optionally paper-trade."""
    from natgas.ingest.weathernext import WeatherNextConfigError

    from kalshi_wx.scan import run_scan

    try:
        record = run_scan(hours=hours)
    except WeatherNextConfigError as exc:
        console.print(f"[yellow]Scan skipped:[/yellow] {exc}")
        raise typer.Exit(1) from exc

    table = Table(title="Kalshi weather scan")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Markets", str(record["n_markets"]))
    table.add_row("Highs", str(record["n_high"]))
    table.add_row("Rain", str(record["n_rain"]))
    table.add_row("Weekend rain", str(record.get("n_weekend") or 0))
    table.add_row("Weekly heat", str(record.get("n_weekly") or 0))
    table.add_row("Near (D+1)", str(record.get("n_tradeable_near", record["n_tradeable"])))
    table.add_row("Far (D+2–10)", str(record.get("n_tradeable_far") or 0))
    table.add_row("Log", record["path"])
    console.print(table)

    tradeable = [c for c in record["candidates"] if c.get("paper_eligible") or c.get("paper_eligible_far")]
    tradeable.sort(key=lambda r: r.get("ev") or 0, reverse=True)
    if tradeable:
        edges = Table(title="Top edges")
        edges.add_column("Book")
        edges.add_column("City")
        edges.add_column("Kind")
        edges.add_column("Date")
        edges.add_column("Side")
        edges.add_column("Model")
        edges.add_column("Book ¢")
        edges.add_column("EV")
        for row in tradeable[:12]:
            books = "/".join(row.get("strategies") or [])
            edges.add_row(
                books or "—",
                row["city"],
                row["kind"],
                row["target_date"],
                row["side"],
                f"{100 * row['model_p']:.0f}¢",
                f"{100 * (row['mid'] or 0):.0f}¢",
                f"{100 * row['ev']:.1f}¢",
            )
        console.print(edges)

    if not skip_paper:
        from kalshi_wx.paper import paper_summary, sync_paper

        ledger = sync_paper(record)
        summary = paper_summary(ledger)
        books = summary.get("by_strategy") or {}
        near = books.get("near") or {}
        far = books.get("far") or {}
        console.print(
            f"[green]Paper near:[/green] {near.get('n_open', 0)} open · "
            f"{near.get('n_settled', 0)} settled · "
            f"P&L ${near.get('cumulative_pnl', 0):.2f}"
        )
        console.print(
            f"[green]Paper far:[/green] {far.get('n_open', 0)} open · "
            f"{far.get('n_settled', 0)} settled · "
            f"P&L ${far.get('cumulative_pnl', 0):.2f}"
        )

    from kalshi_wx.dashboard import generate

    dash = generate(scan=record)
    console.print(f"[green]Kalshi dashboard:[/green] {dash}")


@app.command("paper")
def paper_cmd() -> None:
    """Open new paper tickets from the latest scan and settle closed markets."""
    from kalshi_wx.paper import paper_summary, sync_paper

    ledger = sync_paper()
    summary = paper_summary(ledger)
    table = Table(title="Kalshi weather paper ledger")
    table.add_column("Metric")
    table.add_column("Value")
    books = summary.get("by_strategy") or {}
    for sid, label in (("near", "Near D+1"), ("far", "Far D+2–10")):
        book = books.get(sid) or {}
        table.add_row(f"{label} open", str(book.get("n_open", 0)))
        table.add_row(f"{label} settled", str(book.get("n_settled", 0)))
        table.add_row(f"{label} P&L", f"${book.get('cumulative_pnl', 0):.2f}")
        wr = book.get("win_rate")
        table.add_row(f"{label} win rate", "n/a" if wr is None else f"{wr:.0%}")
    console.print(table)


@app.command("dashboard")
def dashboard_cmd(output: Path = typer.Option(None, help="Override output directory")) -> None:
    """Write the static dashboard to dashboard/kalshi/ (Vercel /kalshi/)."""
    from kalshi_wx.dashboard import generate

    path = generate(output)
    console.print(f"[green]Kalshi dashboard:[/green] {path}")


if __name__ == "__main__":
    app()
