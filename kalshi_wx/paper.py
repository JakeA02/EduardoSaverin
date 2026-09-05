"""CSV paper ledger for Kalshi weather. Settlement from Kalshi `result`, no live orders."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from kalshi_wx.config import (
    N_CONTRACTS,
    PAPER_CLOSE_HOUR_ET,
    PAPER_DIR,
    PAPER_TZ,
    ensure_dirs,
)
from kalshi_wx.horizons import FAR, NEAR, STRATEGIES, policy_by_id
from kalshi_wx.kalshi import KalshiError, get_market
from kalshi_wx.scan import load_latest_scan

ET = ZoneInfo(PAPER_TZ)

LEDGER_PATH = PAPER_DIR / "kalshi_trades.csv"
COLUMNS = [
    "opened_ts",
    "settled_ts",
    "status",
    "strategy",
    "ticker",
    "event_ticker",
    "kind",
    "city",
    "target_date",
    "side",
    "n_contracts",
    "model_p",
    "entry",
    "mid",
    "ev",
    "fee",
    "result",
    "pnl",
    "reasoning_ok",
    "close_time",
    "note",
]


def _blank() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})


def ledger_path() -> Path:
    ensure_dirs()
    if not LEDGER_PATH.exists():
        _blank().to_csv(LEDGER_PATH, index=False)
    return LEDGER_PATH


def load_ledger() -> pd.DataFrame:
    path = ledger_path()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if df.empty:
        return _blank()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    for col in ("n_contracts",):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ("model_p", "entry", "mid", "ev", "fee", "pnl"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def save_ledger(df: pd.DataFrame) -> Path:
    path = ledger_path()
    out = df.copy()
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out[COLUMNS].to_csv(path, index=False)
    return path


def strategy_id(raw: str | None) -> str:
    policy = policy_by_id(str(raw or ""))
    return policy.id if policy else str(raw or "")


def _row_eligible(row: dict, strategy: str) -> bool:
    eligible = row.get("eligible") or {}
    if strategy in eligible:
        return bool(eligible[strategy])
    if strategy == NEAR.id:
        return bool(row.get("paper_eligible"))
    if strategy == FAR.id:
        return bool(row.get("paper_eligible_far"))
    return False


def _dedupe_key(row: dict) -> str:
    """HIGH / weekly heat: one ticket per event. Rain: one per city ticker."""
    kind = row.get("kind") or ""
    if kind in {"high", "weekly_hot"}:
        return row.get("event_ticker") or row["ticker"]
    return row["ticker"]


def _best_per_event(candidates: list[dict], *, strategy: str = NEAR.id) -> list[dict]:
    """Largest EV among that book's contracts, de-duplicated per question."""
    best: dict[str, dict] = {}
    for row in candidates:
        if not _row_eligible(row, strategy):
            continue
        if row.get("side") in (None, "skip"):
            continue
        key = _dedupe_key(row)
        prev = best.get(key)
        if prev is None or float(row.get("ev") or 0) > float(prev.get("ev") or 0):
            best[key] = row
    return list(best.values())


def paper_close_time(target: date) -> datetime:
    """4:00 America/New_York on the calendar day after `target`.

    4am Eastern is 1am Pacific in both standard and daylight time, so the
    LST day is finished for every CONUS station in this book (−5 to −8).
    """
    nxt = target + timedelta(days=1)
    local = datetime(nxt.year, nxt.month, nxt.day, PAPER_CLOSE_HOUR_ET, 0, tzinfo=ET)
    return local.astimezone(UTC)


def _parse_ts(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_date(raw) -> date | None:
    try:
        return raw if isinstance(raw, date) and not isinstance(raw, datetime) else date.fromisoformat(str(raw or ""))
    except ValueError:
        return None


def _opened_et_date(opened_ts: str | None) -> date | None:
    dt = _parse_ts(opened_ts)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET).date()


def _row_close_dt(row) -> datetime | None:
    """Near book: 4am ET on target+1. Far book: Kalshi close_time."""
    if strategy_id(row.get("strategy")) == NEAR.id:
        target = _as_date(row.get("target_date"))
        if target is not None:
            return paper_close_time(target)
    return _parse_ts(row.get("close_time"))


def _paper_close_iso(target: date) -> str:
    return paper_close_time(target).strftime("%Y-%m-%dT%H:%M:%SZ")


def void_same_day_opens(ledger: pd.DataFrame, *, today=None) -> pd.DataFrame:
    """Void near tickets that were opened on or after the target date.

    Those fills already include the realized window. Overnight D+1 tickets
    stay open until 4am Eastern the next morning, then settle as live P&L.
    """
    del today  # open-vs-target uses the fill timestamp, not the scan date
    changed = False
    for i, row in ledger.iterrows():
        if row.get("status") != "open":
            continue
        if strategy_id(row.get("strategy")) != NEAR.id:
            continue
        target = _as_date(row.get("target_date"))
        opened = _opened_et_date(row.get("opened_ts"))
        if target is None or opened is None:
            continue
        if opened >= target:
            ledger.at[i, "status"] = "void"
            ledger.at[i, "note"] = "void · same-day / realized window excluded"
            changed = True
    if changed:
        save_ledger(ledger)
    return ledger


def restore_overnight_near_voids(ledger: pd.DataFrame) -> pd.DataFrame:
    """Put valid D+1 fills back on the live book if they were voided overnight."""
    changed = False
    for i, row in ledger.iterrows():
        if str(row.get("status") or "") != "void":
            continue
        if strategy_id(row.get("strategy")) != NEAR.id:
            continue
        target = _as_date(row.get("target_date"))
        opened = _opened_et_date(row.get("opened_ts"))
        if target is None or opened is None or opened >= target:
            continue
        if _row_result(row) in {"yes", "no"}:
            ledger.at[i, "status"] = "settled"
            ledger.at[i, "note"] = "paper · near · no live order"
        else:
            ledger.at[i, "status"] = "open"
            ledger.at[i, "note"] = "paper · near · no live order"
            ledger.at[i, "close_time"] = _paper_close_iso(target)
            ledger.at[i, "settled_ts"] = ""
        changed = True
    if changed:
        save_ledger(ledger)
    return ledger


def open_from_scan(scan: dict | None = None) -> pd.DataFrame:
    ledger = load_ledger()
    scan = scan or load_latest_scan()
    if not scan:
        return ledger
    open_mask = ledger["status"] == "open"
    open_tickers = set(ledger.loc[open_mask, "ticker"].astype(str))
    open_events: set[tuple[str, str]] = set()
    for _, row in ledger.loc[open_mask].iterrows():
        open_events.add((strategy_id(row.get("strategy")), _dedupe_key(row)))
    ts = scan.get("ts_utc") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows = []
    candidates = scan.get("candidates") or []
    for policy in STRATEGIES:
        picked = sorted(
            _best_per_event(candidates, strategy=policy.id),
            key=lambda r: float(r.get("ev") or 0),
            reverse=True,
        )[: policy.max_tickets]
        for row in picked:
            ticker = row["ticker"]
            event = row.get("event_ticker") or ticker
            key = _dedupe_key(row)
            if ticker in open_tickers:
                continue
            if (policy.id, key) in open_events:
                continue
            already = ledger[ledger["ticker"] == ticker]
            if not already.empty and (already["status"] == "open").any():
                continue
            target = _as_date(row.get("target_date"))
            if policy.id == NEAR.id and target is not None:
                close_time = _paper_close_iso(target)
            else:
                close_time = row.get("close_time") or ""
            new_rows.append(
                {
                    "opened_ts": ts,
                    "settled_ts": "",
                    "status": "open",
                    "strategy": policy.id,
                    "ticker": ticker,
                    "event_ticker": event,
                    "kind": row.get("kind") or "",
                    "city": row.get("city") or "",
                    "target_date": row.get("target_date") or "",
                    "side": row["side"],
                    "n_contracts": N_CONTRACTS,
                    "model_p": row.get("model_p"),
                    "entry": row.get("entry"),
                    "mid": row.get("mid"),
                    "ev": row.get("ev"),
                    "fee": row.get("fee"),
                    "result": "",
                    "pnl": "",
                    "reasoning_ok": "",
                    "close_time": close_time,
                    "note": f"paper · {policy.id} · no live order",
                }
            )
            open_tickers.add(ticker)
            open_events.add((policy.id, key))
    if new_rows:
        ledger = pd.concat([ledger, pd.DataFrame(new_rows)], ignore_index=True)
        save_ledger(ledger)
    return ledger


def _pnl(side: str, entry: float, fee: float, result: str, n: int) -> float:
    win = (side == "yes" and result == "yes") or (side == "no" and result == "no")
    payout = 1.0 if win else 0.0
    return n * (payout - float(entry) - float(fee or 0))


def _reasoning_ok(model_p: float, result: str) -> bool:
    if result == "yes":
        return model_p >= 0.5
    return model_p < 0.5


def _row_result(row) -> str:
    return str(row.get("result") or "").strip().lower()


def _apply_result(ledger: pd.DataFrame, i, result: str, now: datetime) -> None:
    row = ledger.loc[i]
    side = str(row.get("side") or "")
    pnl = _pnl(side, float(row.get("entry") or 0), float(row.get("fee") or 0), result, int(row.get("n_contracts") or 1))
    reasoning = _reasoning_ok(float(row.get("model_p") or 0), result)
    status = str(row.get("status") or "")
    ledger.at[i, "status"] = "void" if status == "void" else "settled"
    ledger.at[i, "result"] = result
    ledger.at[i, "pnl"] = pnl
    ledger.at[i, "reasoning_ok"] = "true" if reasoning else "false"
    ledger.at[i, "settled_ts"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")


def settle_open(*, now: datetime | None = None) -> pd.DataFrame:
    """Fill Kalshi outcomes on live opens and on voided same-day tickets."""
    ledger = load_ledger()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    changed = False
    for i, row in ledger.iterrows():
        status = str(row.get("status") or "")
        if status not in {"open", "void"}:
            continue
        if status == "void" and _row_result(row) in {"yes", "no"}:
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        close_dt = _row_close_dt(row)
        if close_dt is not None and close_dt > now:
            continue
        try:
            market = get_market(ticker)
        except KalshiError:
            continue
        result = (market.get("result") or "").lower()
        if result not in {"yes", "no"}:
            continue
        _apply_result(ledger, i, result, now)
        changed = True
    if changed:
        save_ledger(ledger)
    return ledger


def sync_paper(scan: dict | None = None) -> pd.DataFrame:
    restore_overnight_near_voids(load_ledger())
    void_same_day_opens(load_ledger())
    settle_open()
    return open_from_scan(scan)


def _book_stats(df: pd.DataFrame) -> dict:
    settled = df[df["status"] == "settled"] if not df.empty else df
    open_ = df[df["status"] == "open"] if not df.empty else df
    voided = df[df["status"] == "void"] if not df.empty else df
    scored = df[df.apply(_row_result, axis=1).isin(["yes", "no"])] if not df.empty else df
    pnl = float(pd.to_numeric(settled["pnl"], errors="coerce").fillna(0).sum()) if not settled.empty else 0.0
    scored_pnl = float(pd.to_numeric(scored["pnl"], errors="coerce").fillna(0).sum()) if not scored.empty else 0.0
    wins = 0
    scored_wins = 0
    if not settled.empty:
        wins = int((pd.to_numeric(settled["pnl"], errors="coerce").fillna(0) > 0).sum())
    if not scored.empty:
        scored_wins = int((pd.to_numeric(scored["pnl"], errors="coerce").fillna(0) > 0).sum())
    n_settled = int(len(settled))
    n_scored = int(len(scored))
    reason = None
    if not scored.empty and "reasoning_ok" in scored.columns:
        flag = scored["reasoning_ok"].astype(str).str.lower().isin(["true", "1", "yes"])
        reason = float(flag.mean()) if n_scored else None
    return {
        "n_settled": n_settled,
        "n_open": int(len(open_)),
        "n_void": int(len(voided)),
        "n_scored": n_scored,
        "cumulative_pnl": pnl,
        "scored_pnl": scored_pnl,
        "win_rate": None if n_settled == 0 else wins / n_settled,
        "scored_win_rate": None if n_scored == 0 else scored_wins / n_scored,
        "reasoning_hit_rate": reason,
    }


def paper_summary(ledger: pd.DataFrame | None = None) -> dict:
    df = load_ledger() if ledger is None else ledger
    by_strategy = {}
    for policy in STRATEGIES:
        if df.empty:
            sub = df
        else:
            aliases = policy.aliases()
            sub = df[df["strategy"].map(strategy_id).isin(aliases)]
        by_strategy[policy.id] = {"strategy": policy.id, "label": policy.label, **_book_stats(sub)}
    totals = _book_stats(df)
    return {
        "strategy": f"{NEAR.id}+{FAR.id}",
        "n_settled": totals["n_settled"],
        "n_open": totals["n_open"],
        "n_void": totals["n_void"],
        "n_scored": totals["n_scored"],
        "cumulative_pnl": totals["cumulative_pnl"],
        "scored_pnl": totals["scored_pnl"],
        "win_rate": totals["win_rate"],
        "scored_win_rate": totals["scored_win_rate"],
        "reasoning_hit_rate": totals["reasoning_hit_rate"],
        "by_strategy": by_strategy,
        "trades": df.fillna("").to_dict(orient="records") if not df.empty else [],
    }
