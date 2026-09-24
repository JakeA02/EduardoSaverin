"""Official WNGSR print overlays a lagging EIA Open Data pull."""

from datetime import date
from pathlib import Path

import pandas as pd

from natgas.ingest.eia import merge_wngsr, parse_wngsr, upsert_tracked_actuals

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_wngsr_current_week_and_change() -> None:
    import json

    payload = json.loads((FIXTURES / "wngsr.json").read_text())
    df = parse_wngsr(payload)
    current = df[df["week_ending"] == date(2026, 9, 18)].iloc[0]
    assert current["storage_change_bcf"] == 53.0
    assert current["total"] == 3351.0
    assert current["east"] == 815.0
    prior = df[df["week_ending"] == date(2026, 9, 11)].iloc[0]
    assert prior["total"] == 3298.0
    assert date(2025, 9, 18) not in set(df["week_ending"])


def test_merge_wngsr_fills_missing_print_week() -> None:
    storage = pd.DataFrame(
        {
            "period": pd.to_datetime(["2026-09-11"]),
            "week_ending": [date(2026, 9, 11)],
            "total": [3298.0],
            "storage_change_bcf": [44.0],
        }
    )
    wngsr = pd.DataFrame(
        {
            "period": pd.to_datetime(["2026-09-11", "2026-09-18"]),
            "week_ending": [date(2026, 9, 11), date(2026, 9, 18)],
            "total": [3298.0, 3351.0],
            "storage_change_bcf": [pd.NA, 53.0],
        }
    )
    merged = merge_wngsr(storage, wngsr)
    row = merged[merged["week_ending"] == date(2026, 9, 18)].iloc[0]
    assert row["storage_change_bcf"] == 53.0
    assert row["total"] == 3351.0


def test_tracked_actuals_roundtrip(tmp_path, monkeypatch) -> None:
    from natgas.ingest import eia as eia_mod

    csv_path = tmp_path / "actuals.csv"
    monkeypatch.setattr(eia_mod, "EIA_ACTUALS_CSV", csv_path)
    incoming = pd.DataFrame(
        {
            "week_ending": [date(2026, 9, 18)],
            "storage_change_bcf": [53.0],
            "total": [3351.0],
        }
    )
    path = upsert_tracked_actuals(incoming, source="wngsr")
    assert path == csv_path
    assert path.exists()
    loaded = eia_mod.load_tracked_actuals()
    assert loaded.iloc[0]["week_ending"] == date(2026, 9, 18)
    assert loaded.iloc[0]["storage_change_bcf"] == 53.0
    assert loaded.iloc[0]["source"] == "wngsr"
