"""WeatherNext hour aggregation and dashboard generation for Kalshi weather."""

from datetime import date
from pathlib import Path

from kalshi_wx.dashboard import generate
from kalshi_wx.forecast import daily_from_hours, hour_lst_date
from kalshi_wx.forecast import _dedupe_hours
from kalshi_wx.paper import (
    _best_per_event,
    _pnl,
    paper_close_time,
    paper_summary,
    restore_overnight_near_voids,
    void_same_day_opens,
)
from kalshi_wx.stations import BY_CITY


def _hour(start_utc: str, temp_f: float, pop: int = 0, qpf_mm: float = 0.0) -> dict:
    return {
        "interval": {"startTime": start_utc, "endTime": start_utc},
        "temperature": {"degrees": (temp_f - 32) * 5 / 9, "unit": "CELSIUS"},
        "precipitation": {
            "probability": {"percent": pop, "type": "RAIN"},
            "qpf": {"quantity": qpf_mm, "unit": "MILLIMETERS"},
        },
    }


def test_lst_date_uses_standard_offset_not_civil_clock() -> None:
    # 04:00Z on Sep 4 is 23:00 EST (LST) Sep 3 for NYC (UTC-5).
    item = _hour("2026-09-04T04:00:00Z", 70)
    assert hour_lst_date(item, -5) == date(2026, 9, 3)
    item2 = _hour("2026-09-04T05:00:00Z", 70)
    assert hour_lst_date(item2, -5) == date(2026, 9, 4)


def test_daily_tmax_qpf_from_hours() -> None:
    nyc = BY_CITY["NYC"]
    hours = [
        _hour("2026-09-04T16:00:00Z", 80, pop=10, qpf_mm=0.0),  # 11:00 LST
        _hour("2026-09-04T20:00:00Z", 88, pop=40, qpf_mm=2.54),  # 15:00 LST
        _hour("2026-09-04T23:00:00Z", 84, pop=20, qpf_mm=0.0),
    ]
    days = daily_from_hours(hours, nyc, issued=date(2026, 9, 4))
    assert len(days) == 1
    d = days[0]
    assert d.target_date == date(2026, 9, 4)
    assert abs(d.tmax_f - 88) < 0.2
    assert abs(d.qpf_in - 0.1) < 0.01
    assert d.n_hours == 3


def test_dedupe_hours_keeps_latest_duplicate_timestamp() -> None:
    a = _hour("2026-09-04T16:00:00Z", 70)
    b = _hour("2026-09-04T16:00:00Z", 80)
    c = _hour("2026-09-04T17:00:00Z", 75)
    out = _dedupe_hours([a, b, c])
    assert len(out) == 2
    assert hour_lst_date(out[0], -5)  # still parseable


def test_generate_writes_separate_kalshi_folder(tmp_path: Path) -> None:
    path = generate(tmp_path)
    assert path.exists()
    html = path.read_text()
    assert "Kalshi weather vs WeatherNext 3" in html
    assert "US natural gas storage forecast" not in html
    assert "near" in html and "far" in html
    assert "Two paper books" in html
    assert "scored when Kalshi settles" in html
    assert "Resolution scorecard" in html
    assert (tmp_path / "data.json").exists()


def test_one_paper_ticket_per_event() -> None:
    rows = [
        {"event_ticker": "E1", "ticker": "A", "kind": "high", "side": "yes", "ev": 0.08, "paper_eligible": True},
        {"event_ticker": "E1", "ticker": "B", "kind": "high", "side": "no", "ev": 0.12, "paper_eligible": True},
        {"event_ticker": "E2", "ticker": "C", "kind": "high", "side": "skip", "ev": 0.0, "paper_eligible": False},
    ]
    best = _best_per_event(rows)
    assert len(best) == 1
    assert best[0]["ticker"] == "B"


def test_best_per_event_is_keyed_by_strategy() -> None:
    rows = [
        {
            "event_ticker": "E-NEAR",
            "ticker": "N1",
            "kind": "high",
            "side": "yes",
            "ev": 0.09,
            "eligible": {"near": True, "far": False},
        },
        {
            "event_ticker": "E-FAR",
            "ticker": "F1",
            "kind": "high",
            "side": "no",
            "ev": 0.11,
            "eligible": {"near": False, "far": True},
        },
        {
            "event_ticker": "E-FAR",
            "ticker": "F2",
            "kind": "high",
            "side": "yes",
            "ev": 0.07,
            "eligible": {"near": False, "far": True},
        },
    ]
    near = _best_per_event(rows, strategy="near")
    far = _best_per_event(rows, strategy="far")
    assert [r["ticker"] for r in near] == ["N1"]
    assert [r["ticker"] for r in far] == ["F1"]


def test_weekend_rain_dedupes_by_ticker_not_shared_event() -> None:
    rows = [
        {
            "event_ticker": "KXRAINWKND-26SEP05",
            "ticker": "KXRAINWKND-26SEP05-HOU",
            "kind": "rain_weekend",
            "side": "yes",
            "ev": 0.20,
            "eligible": {"near": False, "far": True},
        },
        {
            "event_ticker": "KXRAINWKND-26SEP05",
            "ticker": "KXRAINWKND-26SEP05-PHIL",
            "kind": "rain_weekend",
            "side": "yes",
            "ev": 0.40,
            "eligible": {"near": False, "far": True},
        },
    ]
    far = _best_per_event(rows, strategy="far")
    assert {r["ticker"] for r in far} == {
        "KXRAINWKND-26SEP05-HOU",
        "KXRAINWKND-26SEP05-PHIL",
    }


def test_paper_close_is_4am_eastern_on_target_plus_one() -> None:
    from datetime import UTC, datetime

    summer = paper_close_time(date(2026, 9, 4))
    assert summer == datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    winter = paper_close_time(date(2026, 1, 4))
    assert winter == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def test_void_same_day_skips_overnight_d1_and_far_book(monkeypatch) -> None:
    import pandas as pd

    monkeypatch.setattr("kalshi_wx.paper.save_ledger", lambda df: df)
    ledger = pd.DataFrame(
        [
            {
                "status": "open",
                "strategy": "near",
                "target_date": "2026-09-04",
                "opened_ts": "2026-09-03T19:55:41Z",
                "note": "paper · near · no live order",
                "settled_ts": "",
            },
            {
                "status": "open",
                "strategy": "wx-kalshi-v0.1",
                "target_date": "2026-09-03",
                "opened_ts": "2026-09-03T19:55:41Z",
                "note": "",
                "settled_ts": "",
            },
            {
                "status": "open",
                "strategy": "far",
                "target_date": "2026-09-03",
                "opened_ts": "2026-09-03T20:20:42Z",
                "note": "paper · far · no live order",
                "settled_ts": "",
            },
        ]
    )
    out = void_same_day_opens(ledger, today=date(2026, 9, 4))
    assert out.loc[0, "status"] == "open"
    assert out.loc[1, "status"] == "void"
    assert out.loc[2, "status"] == "open"


def test_restore_overnight_near_voids(monkeypatch) -> None:
    import pandas as pd

    monkeypatch.setattr("kalshi_wx.paper.save_ledger", lambda df: df)
    ledger = pd.DataFrame(
        [
            {
                "status": "void",
                "strategy": "near",
                "target_date": "2026-09-04",
                "opened_ts": "2026-09-03T19:55:41Z",
                "result": "",
                "note": "void · same-day / realized window excluded",
                "close_time": "2026-09-05T05:00:00Z",
                "settled_ts": "2026-09-04T07:23:11Z",
            },
            {
                "status": "void",
                "strategy": "near",
                "target_date": "2026-09-03",
                "opened_ts": "2026-09-03T19:55:41Z",
                "result": "yes",
                "note": "void · same-day / realized window excluded",
                "settled_ts": "2026-09-04T12:17:48Z",
            },
            {
                "status": "void",
                "strategy": "near",
                "target_date": "2026-09-04",
                "opened_ts": "2026-09-03T19:55:41Z",
                "result": "no",
                "pnl": 0.5,
                "note": "void · same-day / realized window excluded",
                "settled_ts": "2026-09-05T12:00:00Z",
            },
        ]
    )
    out = restore_overnight_near_voids(ledger)
    assert out.loc[0, "status"] == "open"
    assert str(out.loc[0, "close_time"]) == "2026-09-05T08:00:00Z"
    assert out.loc[1, "status"] == "void"
    assert out.loc[2, "status"] == "settled"


def test_paper_summary_groups_legacy_near_rows() -> None:
    import pandas as pd

    ledger = pd.DataFrame(
        [
            {"status": "open", "strategy": "wx-kalshi-v0.1", "pnl": "", "reasoning_ok": "", "result": ""},
            {"status": "open", "strategy": "far", "pnl": "", "reasoning_ok": "", "result": ""},
            {"status": "settled", "strategy": "near", "pnl": 0.2, "reasoning_ok": "true", "result": "yes"},
        ]
    )
    summary = paper_summary(ledger)
    assert summary["by_strategy"]["near"]["n_open"] == 1
    assert summary["by_strategy"]["near"]["n_settled"] == 1
    assert summary["by_strategy"]["near"]["n_scored"] == 1
    assert summary["by_strategy"]["far"]["n_open"] == 1
    assert summary["by_strategy"]["far"]["n_settled"] == 0


def test_paper_summary_scores_voided_tickets() -> None:
    import pandas as pd

    ledger = pd.DataFrame(
        [
            {
                "status": "void",
                "strategy": "wx-kalshi-v0.1",
                "pnl": -0.25,
                "reasoning_ok": "false",
                "result": "yes",
            },
            {
                "status": "void",
                "strategy": "near",
                "pnl": 0.70,
                "reasoning_ok": "true",
                "result": "no",
            },
            {
                "status": "settled",
                "strategy": "far",
                "pnl": 0.10,
                "reasoning_ok": "true",
                "result": "yes",
            },
        ]
    )
    summary = paper_summary(ledger)
    near = summary["by_strategy"]["near"]
    far = summary["by_strategy"]["far"]
    assert near["n_settled"] == 0
    assert near["n_void"] == 2
    assert near["n_scored"] == 2
    assert near["cumulative_pnl"] == 0.0
    assert abs(near["scored_pnl"] - 0.45) < 1e-9
    assert near["scored_win_rate"] == 0.5
    assert near["reasoning_hit_rate"] == 0.5
    assert far["n_settled"] == 1
    assert far["n_scored"] == 1


def test_settle_open_scores_void_without_promoting_to_settled(monkeypatch) -> None:
    from datetime import UTC, datetime

    import pandas as pd

    from kalshi_wx.paper import settle_open

    ledger = pd.DataFrame(
        [
            {
                "status": "void",
                "strategy": "near",
                "ticker": "KXHIGHNY-26SEP03-B83.5",
                "side": "no",
                "n_contracts": 1,
                "model_p": 0.23,
                "entry": 0.24,
                "fee": 0.01,
                "result": "",
                "pnl": None,
                "reasoning_ok": "",
                "close_time": "2026-09-04T05:00:00Z",
                "settled_ts": "",
                "note": "void · same-day / realized window excluded",
            }
        ]
    )
    monkeypatch.setattr("kalshi_wx.paper.load_ledger", lambda: ledger)
    monkeypatch.setattr("kalshi_wx.paper.save_ledger", lambda df: df)
    monkeypatch.setattr("kalshi_wx.paper.get_market", lambda ticker: {"result": "no"})
    out = settle_open(now=datetime(2026, 9, 4, 12, tzinfo=UTC))
    assert out.loc[0, "status"] == "void"
    assert out.loc[0, "result"] == "no"
    assert out.loc[0, "reasoning_ok"] == "true"
    assert float(out.loc[0, "pnl"]) > 0
    assert str(out.loc[0, "settled_ts"]).startswith("2026-09-04")


def test_settle_open_waits_until_4am_et(monkeypatch) -> None:
    from datetime import UTC, datetime

    import pandas as pd

    from kalshi_wx.paper import settle_open

    ledger = pd.DataFrame(
        [
            {
                "status": "open",
                "strategy": "near",
                "ticker": "KXHIGHNY-26SEP04-B86.5",
                "target_date": "2026-09-04",
                "side": "yes",
                "n_contracts": 1,
                "model_p": 0.29,
                "entry": 0.07,
                "fee": 0.005,
                "result": "",
                "pnl": None,
                "reasoning_ok": "",
                "close_time": "2026-09-05T05:00:00Z",
                "settled_ts": "",
            }
        ]
    )
    called = {"n": 0}

    def _market(_ticker: str) -> dict:
        called["n"] += 1
        return {"result": "yes"}

    monkeypatch.setattr("kalshi_wx.paper.load_ledger", lambda: ledger)
    monkeypatch.setattr("kalshi_wx.paper.save_ledger", lambda df: df)
    monkeypatch.setattr("kalshi_wx.paper.get_market", _market)
    before = settle_open(now=datetime(2026, 9, 5, 7, 59, tzinfo=UTC))
    assert called["n"] == 0
    assert before.loc[0, "status"] == "open"
    after = settle_open(now=datetime(2026, 9, 5, 8, 0, tzinfo=UTC))
    assert called["n"] == 1
    assert after.loc[0, "status"] == "settled"
    assert after.loc[0, "result"] == "yes"


def test_settle_open_skips_void_before_close(monkeypatch) -> None:
    from datetime import UTC, datetime

    import pandas as pd

    from kalshi_wx.paper import settle_open

    ledger = pd.DataFrame(
        [
            {
                "status": "void",
                "strategy": "near",
                "ticker": "KXHIGHNY-26SEP04-B86.5",
                "target_date": "2026-09-04",
                "side": "yes",
                "n_contracts": 1,
                "model_p": 0.29,
                "entry": 0.07,
                "fee": 0.005,
                "result": "",
                "pnl": "",
                "reasoning_ok": "",
                "close_time": "2026-09-05T05:00:00Z",
                "settled_ts": "",
            }
        ]
    )
    called = {"n": 0}

    def _market(_ticker: str) -> dict:
        called["n"] += 1
        return {"result": "yes"}

    monkeypatch.setattr("kalshi_wx.paper.load_ledger", lambda: ledger)
    monkeypatch.setattr("kalshi_wx.paper.save_ledger", lambda df: df)
    monkeypatch.setattr("kalshi_wx.paper.get_market", _market)
    out = settle_open(now=datetime(2026, 9, 4, 12, tzinfo=UTC))
    assert called["n"] == 0
    assert out.loc[0, "result"] == ""
    assert out.loc[0, "status"] == "void"


def test_paper_pnl_yes_win_and_no_loss() -> None:
    # Buy YES at 40¢, fee 1.68¢, result yes → 1 - 0.40 - 0.0168
    assert abs(_pnl("yes", 0.40, 0.0168, "yes", 1) - (1 - 0.40 - 0.0168)) < 1e-9
    assert _pnl("no", 0.30, 0.01, "yes", 1) < 0
