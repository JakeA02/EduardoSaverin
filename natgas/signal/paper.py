"""CSV paper ledger. Settlement-price fills, no discretionary overrides."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from natgas.calendar import report_release_datetime
from natgas.config import INTERIM_DIR, PAPER_DIR, ensure_dirs
from natgas.ingest.consensus import load_consensus
from natgas.predictions import load_json_log
from natgas.signal.prices import entry_exit_fills, load_or_fetch_prices
from natgas.signal.translate import (
    MAX_CONSECUTIVE_LOSSES,
    STRATEGY,
    TradeSignal,
    build_signal,
    pnl_usd,
    reasoning_validated,
)

LEDGER_PATH = PAPER_DIR / "trades.csv"
COLUMNS = [
    "week_ending",
    "strategy",
    "status",
    "side",
    "n_contracts",
    "instrument",
    "predicted",
    "consensus",
    "predicted_surprise",
    "actual",
    "actual_surprise",
    "entry_date",
    "entry_price",
    "exit_date",
    "exit_price",
    "pnl_usd",
    "reasoning_ok",
    "note",
    "source",
    "opened_ts",
    "settled_ts",
    "size_reason",
]


def _blank_ledger() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in COLUMNS})


def ledger_path() -> Path:
    ensure_dirs()
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    if not LEDGER_PATH.exists():
        _blank_ledger().to_csv(LEDGER_PATH, index=False)
    return LEDGER_PATH


def load_ledger() -> pd.DataFrame:
    path = ledger_path()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if df.empty:
        return _blank_ledger()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["week_ending"] = pd.to_datetime(df["week_ending"], errors="coerce").dt.date
    for col in ("n_contracts",):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in (
        "predicted",
        "consensus",
        "predicted_surprise",
        "actual",
        "actual_surprise",
        "entry_price",
        "exit_price",
        "pnl_usd",
    ):
        df[col] = pd.to_numeric(df[col].replace("", pd.NA), errors="coerce")
    df["reasoning_ok"] = df["reasoning_ok"].map(
        lambda v: None if v in {"", "None", "nan"} else v.strip().lower() in {"true", "1", "yes"}
    )
    return df[COLUMNS]


def save_ledger(df: pd.DataFrame) -> Path:
    path = ledger_path()
    out = df.copy()
    if not out.empty:
        out["week_ending"] = pd.to_datetime(out["week_ending"]).dt.strftime("%Y-%m-%d")
        for col in ("entry_date", "exit_date"):
            if col in out.columns:
                out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
    out = out[COLUMNS].sort_values("week_ending")
    out.to_csv(path, index=False)
    return path


def _empty_row(week: date, signal: TradeSignal, source: str, now: str) -> dict:
    return {
        "week_ending": week,
        "strategy": signal.strategy,
        "status": "skipped" if signal.side == "flat" else "open",
        "side": signal.side,
        "n_contracts": signal.n_contracts,
        "instrument": signal.instrument,
        "predicted": None,
        "consensus": None,
        "predicted_surprise": signal.predicted_surprise,
        "actual": None,
        "actual_surprise": None,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "pnl_usd": None,
        "reasoning_ok": None,
        "note": signal.size_reason,
        "source": source,
        "opened_ts": now,
        "settled_ts": None,
        "size_reason": signal.size_reason,
    }


def _load_actuals() -> pd.DataFrame:
    try:
        from natgas.ingest.eia import load_latest_vintage

        eia = load_latest_vintage()
        eia["week_ending"] = pd.to_datetime(eia["week_ending"]).dt.date
        return eia[["week_ending", "storage_change_bcf"]].dropna()
    except FileNotFoundError:
        pass
    try:
        from natgas.features.balance import load_panel

        panel = load_panel()
        panel["week_ending"] = pd.to_datetime(panel["week_ending"]).dt.date
        return panel[["week_ending", "storage_change_bcf"]].dropna()
    except (FileNotFoundError, OSError):
        return pd.DataFrame(columns=["week_ending", "storage_change_bcf"])


def _is_frozen(week: date, now: datetime) -> bool:
    release = report_release_datetime(week)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return current.astimezone(release.tzinfo) >= release


def consecutive_losses(ledger: pd.DataFrame) -> int:
    settled = ledger[ledger["status"] == "settled"].sort_values("week_ending")
    n = 0
    for pnl in reversed(settled["pnl_usd"].tolist()):
        if pd.isna(pnl) or float(pnl) >= 0:
            break
        n += 1
    return n


def _upsert_open(ledger: pd.DataFrame, row: dict) -> pd.DataFrame:
    week = row["week_ending"]
    existing = ledger[ledger["week_ending"] == week]
    if existing.empty:
        return pd.concat([ledger, pd.DataFrame([row])], ignore_index=True)
    idx = existing.index[-1]
    if str(ledger.at[idx, "status"]) == "settled":
        return ledger
    for key, value in row.items():
        if key in {"opened_ts", "source"} and pd.notna(ledger.at[idx, key]):
            continue
        ledger.at[idx, key] = value
    return ledger


def _predictions_frame(*, hindcast: bool) -> pd.DataFrame:
    live = load_json_log()
    if not live.empty:
        live = live.rename(
            columns={"target_week_ending": "week_ending", "predicted_change_bcf": "predicted"}
        )
        live["source"] = "live"
        live = live[["week_ending", "predicted", "source"]]
    frames = [] if live.empty else [live]
    if hindcast:
        backtest = INTERIM_DIR / "backtest.parquet"
        if backtest.exists():
            hist = pd.read_parquet(backtest)
            hist["week_ending"] = pd.to_datetime(hist["week_ending"]).dt.date
            hist = hist.rename(columns={"predicted": "predicted"})
            hist["source"] = "hindcast"
            hist = hist[["week_ending", "predicted", "source"]]
            if not live.empty:
                hist = hist[~hist["week_ending"].isin(set(live["week_ending"]))]
            frames.append(hist)
    if not frames:
        return pd.DataFrame(columns=["week_ending", "predicted", "source"])
    return pd.concat(frames, ignore_index=True)


def sync_paper(*, hindcast: bool = False, now: datetime | None = None) -> pd.DataFrame:
    """Open/revise unfrozen trades and settle weeks that have a print + prices."""
    now = now or datetime.now(UTC)
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger = load_ledger()
    preds = _predictions_frame(hindcast=hindcast)
    consensus = load_consensus()
    actuals = _load_actuals()
    prices = load_or_fetch_prices(refresh=True)

    if preds.empty or consensus.empty:
        return ledger

    merged = preds.merge(consensus[["week_ending", "consensus_median"]], on="week_ending", how="inner")
    if not actuals.empty:
        merged = merged.merge(actuals, on="week_ending", how="left")
    else:
        merged["storage_change_bcf"] = pd.NA

    standing_down = consecutive_losses(ledger) >= MAX_CONSECUTIVE_LOSSES

    for _, item in merged.iterrows():
        week: date = item["week_ending"]
        predicted = float(item["predicted"])
        consensus_v = float(item["consensus_median"])
        signal = build_signal(predicted, consensus_v)
        source = str(item.get("source") or "live")
        existing = ledger[ledger["week_ending"] == week]
        already_settled = (not existing.empty) and str(existing.iloc[-1]["status"]) == "settled"
        if already_settled:
            continue

        frozen = _is_frozen(week, now)
        can_revise = existing.empty or not frozen
        if can_revise:
            if standing_down and source == "live" and signal.side != "flat":
                signal = TradeSignal(
                    strategy=signal.strategy,
                    instrument=signal.instrument,
                    side="flat",
                    n_contracts=0,
                    predicted_surprise=signal.predicted_surprise,
                    size_reason=(
                        f"stand-down after {MAX_CONSECUTIVE_LOSSES} consecutive losses; "
                        + signal.size_reason
                    ),
                )
            row = _empty_row(week, signal, source, now_s)
            row["predicted"] = predicted
            row["consensus"] = consensus_v
            ledger = _upsert_open(ledger, row)
            existing = ledger[ledger["week_ending"] == week]

        actual = item.get("storage_change_bcf")
        fills = entry_exit_fills(prices, week) if frozen else None
        if frozen and pd.notna(actual) and fills is not None and not existing.empty:
            idx = existing.index[-1]
            side = str(ledger.at[idx, "side"])
            n = int(ledger.at[idx, "n_contracts"] or 0)
            actual_f = float(actual)
            actual_surprise = actual_f - consensus_v
            ledger.at[idx, "actual"] = actual_f
            ledger.at[idx, "actual_surprise"] = actual_surprise
            ledger.at[idx, "entry_date"] = fills["entry_date"].isoformat()
            ledger.at[idx, "entry_price"] = fills["entry_price"]
            ledger.at[idx, "exit_date"] = fills["exit_date"].isoformat()
            ledger.at[idx, "exit_price"] = fills["exit_price"]
            ledger.at[idx, "predicted"] = predicted
            ledger.at[idx, "consensus"] = consensus_v
            if n > 0 and side in {"long", "short"}:
                pnl = pnl_usd(side, n, fills["entry_price"], fills["exit_price"])
                ok = reasoning_validated(float(ledger.at[idx, "predicted_surprise"]), actual_surprise)
                ledger.at[idx, "pnl_usd"] = round(pnl, 2)
                ledger.at[idx, "reasoning_ok"] = ok
                hit = "reasoning held" if ok else "reasoning failed (lucky P&L possible)"
                ledger.at[idx, "note"] = f"{ledger.at[idx, 'size_reason']}; {hit}"
                ledger.at[idx, "status"] = "settled"
            else:
                ledger.at[idx, "pnl_usd"] = 0.0
                ledger.at[idx, "reasoning_ok"] = reasoning_validated(
                    float(ledger.at[idx, "predicted_surprise"]), actual_surprise
                )
                ledger.at[idx, "status"] = "settled"
                ledger.at[idx, "note"] = str(ledger.at[idx, "size_reason"]) + "; flat, $0"
            ledger.at[idx, "settled_ts"] = now_s

    save_ledger(ledger)
    return load_ledger()


def paper_summary(ledger: pd.DataFrame | None = None) -> dict:
    df = load_ledger() if ledger is None else ledger
    settled = df[df["status"] == "settled"]
    n = len(settled)
    pnl = float(settled["pnl_usd"].sum()) if n else 0.0
    wins = int((settled["pnl_usd"] > 0).sum()) if n else 0
    reasoning = settled.dropna(subset=["reasoning_ok"])
    reason_hit = float(reasoning["reasoning_ok"].mean()) if not reasoning.empty else None
    open_rows = df[df["status"] == "open"]
    latest_open = None
    if not open_rows.empty:
        row = open_rows.sort_values("week_ending").iloc[-1]
        latest_open = {
            "week_ending": row["week_ending"].isoformat()
            if hasattr(row["week_ending"], "isoformat")
            else str(row["week_ending"]),
            "side": row["side"],
            "n_contracts": int(row["n_contracts"]),
            "predicted_surprise": float(row["predicted_surprise"])
            if pd.notna(row["predicted_surprise"])
            else None,
        }
    return {
        "strategy": STRATEGY,
        "n_settled": n,
        "n_open": len(open_rows),
        "n_skipped": int((df["status"] == "skipped").sum()),
        "cumulative_pnl_usd": round(pnl, 2),
        "win_rate": (wins / n) if n else None,
        "reasoning_hit_rate": reason_hit,
        "consecutive_losses": consecutive_losses(df),
        "latest_open": latest_open,
        "trades": _trades_payload(df),
    }


def _trades_payload(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df.sort_values("week_ending").iterrows():
        week = row["week_ending"]
        rows.append(
            {
                "week_ending": week.isoformat() if hasattr(week, "isoformat") else str(week),
                "status": row["status"],
                "side": row["side"],
                "n_contracts": int(row["n_contracts"]) if pd.notna(row["n_contracts"]) else 0,
                "predicted_surprise": _num(row.get("predicted_surprise")),
                "actual_surprise": _num(row.get("actual_surprise")),
                "entry_price": _num(row.get("entry_price")),
                "exit_price": _num(row.get("exit_price")),
                "pnl_usd": _num(row.get("pnl_usd")),
                "reasoning_ok": None if pd.isna(row.get("reasoning_ok")) else bool(row["reasoning_ok"]),
                "note": None if pd.isna(row.get("note")) else str(row["note"]),
                "source": None if pd.isna(row.get("source")) else str(row["source"]),
            }
        )
    return rows


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)
