"""Consensus HTML parsers and merge rules. Network is not required."""

from datetime import date
from pathlib import Path

import pandas as pd

from natgas.ingest.consensus import (
    merge_fetched,
    parse_bcf,
    parse_investing_history,
    parse_te_calendar,
    parse_te_news,
    release_to_week_ending,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_bcf_units() -> None:
    assert parse_bcf("19Bcf") == 19.0
    assert parse_bcf("15.00B") == 15.0
    assert parse_bcf("market expectations of 20 bcf") == 20.0
    assert parse_bcf("-58Bcf") == -58.0
    assert parse_bcf("") is None
    assert parse_bcf("  ") is None


def test_release_thursday_maps_to_prior_friday() -> None:
    assert release_to_week_ending(date(2026, 8, 27)) == date(2026, 8, 21)


def test_te_calendar_fixture() -> None:
    html = (FIXTURES / "tradingeconomics_ng.html").read_text()
    df = parse_te_calendar(html)
    weeks = dict(zip(df["week_ending"], df["consensus_median"], strict=True))
    assert weeks[date(2026, 8, 14)] == 19.0
    assert weeks[date(2026, 8, 21)] == 20.0
    assert date(2026, 8, 28) not in weeks  # upcoming print, no consensus yet
    assert (df["source"] == "tradingeconomics").all()


def test_te_news_backfill() -> None:
    html = (FIXTURES / "tradingeconomics_ng.html").read_text()
    df = parse_te_news(html)
    weeks = dict(zip(df["week_ending"], df["consensus_median"], strict=True))
    assert weeks[date(2026, 8, 7)] == 31.0
    assert weeks[date(2026, 8, 14)] == 19.0
    assert weeks[date(2026, 8, 21)] == 20.0


def test_investing_history_fixture() -> None:
    html = (FIXTURES / "investing_ng.html").read_text()
    df = parse_investing_history(html)
    weeks = dict(zip(df["week_ending"], df["consensus_median"], strict=True))
    assert weeks[date(2026, 8, 21)] == 19.0
    assert weeks[date(2026, 8, 14)] == 15.0
    assert weeks[date(2026, 8, 7)] == 31.0


def test_merge_prefers_fetched_over_stale_auto_but_keeps_manual() -> None:
    existing = pd.DataFrame(
        {
            "week_ending": [date(2026, 8, 14), date(2026, 8, 21)],
            "consensus_median": [99.0, 18.0],
            "consensus_low": [None, None],
            "consensus_high": [None, None],
            "source": ["manual", "tradingeconomics"],
        }
    )
    fetched = pd.DataFrame(
        {
            "week_ending": [date(2026, 8, 14), date(2026, 8, 21), date(2026, 8, 7)],
            "consensus_median": [19.0, 20.0, 31.0],
            "consensus_low": [None, None, None],
            "consensus_high": [None, None, None],
            "source": ["tradingeconomics"] * 3,
        }
    )
    merged = merge_fetched(existing, fetched)
    by_week = merged.set_index("week_ending")
    assert by_week.loc[date(2026, 8, 14), "consensus_median"] == 99.0
    assert by_week.loc[date(2026, 8, 14), "source"] == "manual"
    assert by_week.loc[date(2026, 8, 21), "consensus_median"] == 20.0
    assert by_week.loc[date(2026, 8, 7), "consensus_median"] == 31.0
