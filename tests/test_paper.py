"""Paper-trade signal rules and ledger merge. Network not required."""

from datetime import date

import pandas as pd

from natgas.signal.paper import consecutive_losses
from natgas.signal.translate import build_signal, pnl_usd, reasoning_validated, size_contracts


def test_deadband_is_flat() -> None:
    sig = build_signal(predicted=22.0, consensus=20.0)
    assert sig.side == "flat"
    assert sig.n_contracts == 0


def test_more_injection_than_street_is_short() -> None:
    sig = build_signal(predicted=40.0, consensus=20.0)
    assert sig.side == "short"
    assert sig.n_contracts == 2
    assert sig.predicted_surprise == 20.0


def test_tighter_than_street_is_long_one_contract() -> None:
    sig = build_signal(predicted=10.0, consensus=20.0)
    assert sig.side == "long"
    assert sig.n_contracts == 1


def test_size_breakpoints() -> None:
    assert size_contracts(5.0) == 0
    assert size_contracts(5.1) == 1
    assert size_contracts(15.0) == 1
    assert size_contracts(15.1) == 2


def test_qg_pnl_math() -> None:
    # Long 1 QG, +$0.10 → +$250 minus $5 commission
    assert pnl_usd("long", 1, 3.00, 3.10) == 245.0
    # Short 2 QG, price +$0.20 → −$1,000 minus $10
    assert pnl_usd("short", 2, 3.00, 3.20) == -1010.0
    assert pnl_usd("flat", 0, 3.00, 3.10) == 0.0


def test_reasoning_is_surprise_direction_not_pnl() -> None:
    assert reasoning_validated(-8.0, -3.0) is True
    assert reasoning_validated(-8.0, 4.0) is False


def test_consecutive_losses_from_tail() -> None:
    ledger = pd.DataFrame(
        {
            "status": ["settled", "settled", "settled", "open"],
            "pnl_usd": [100.0, -50.0, -80.0, None],
            "week_ending": [date(2026, 8, 7), date(2026, 8, 14), date(2026, 8, 21), date(2026, 8, 28)],
        }
    )
    assert consecutive_losses(ledger) == 2


def test_save_roundtrip(tmp_path, monkeypatch) -> None:
    from natgas.signal import paper as paper_mod

    monkeypatch.setattr(paper_mod, "LEDGER_PATH", tmp_path / "trades.csv")
    monkeypatch.setattr(paper_mod, "PAPER_DIR", tmp_path)
    df = pd.DataFrame(
        [
            {
                "week_ending": date(2026, 8, 21),
                "strategy": "surprise-qg-v0.1",
                "status": "settled",
                "side": "short",
                "n_contracts": 1,
                "instrument": "QG",
                "predicted": 25.0,
                "consensus": 20.0,
                "predicted_surprise": 5.1,
                "actual": 15.0,
                "actual_surprise": -5.0,
                "entry_date": date(2026, 8, 26),
                "entry_price": 2.90,
                "exit_date": date(2026, 8, 27),
                "exit_price": 2.80,
                "pnl_usd": 245.0,
                "reasoning_ok": False,
                "note": "test",
                "source": "hindcast",
                "opened_ts": "2026-08-28T00:00:00Z",
                "settled_ts": "2026-08-28T00:00:00Z",
                "size_reason": "standard short",
            }
        ]
    )
    paper_mod.save_ledger(df)
    loaded = paper_mod.load_ledger()
    assert len(loaded) == 1
    assert loaded.iloc[0]["side"] == "short"
    assert loaded.iloc[0]["week_ending"] == date(2026, 8, 21)
