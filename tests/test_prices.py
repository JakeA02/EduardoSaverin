"""NG price merge and fill helpers."""

from datetime import date

import pandas as pd

from natgas.signal.prices import _merge_closes, close_on_or_before, entry_exit_fills


def test_merge_prefers_earlier_frame_on_duplicate_dates() -> None:
    eia = pd.DataFrame({"date": [date(2026, 8, 25)], "close": [2.70]})
    yahoo = pd.DataFrame(
        {
            "date": [date(2026, 8, 25), date(2026, 8, 27)],
            "close": [2.71, 2.91],
        }
    )
    merged = _merge_closes(eia, yahoo)
    assert float(merged.loc[merged["date"] == date(2026, 8, 25), "close"].iloc[0]) == 2.70
    assert float(merged.loc[merged["date"] == date(2026, 8, 27), "close"].iloc[0]) == 2.91


def test_entry_exit_uses_fallback_thursday_close() -> None:
    week = date(2026, 8, 21)
    prices = pd.DataFrame(
        {
            "date": [
                date(2026, 8, 20),
                date(2026, 8, 21),
                date(2026, 8, 25),
                date(2026, 8, 26),
                date(2026, 8, 27),
            ],
            "close": [2.94, 2.82, 2.70, 2.84, 2.91],
        }
    )
    fills = entry_exit_fills(prices, week)
    assert fills is not None
    assert fills["entry_date"] == date(2026, 8, 26)
    assert fills["exit_date"] == date(2026, 8, 27)
    assert close_on_or_before(prices, date(2026, 8, 27)) == (date(2026, 8, 27), 2.91)
