"""Synthetic Phase 3 walk-forward: forecast weather should score finite MAE."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from natgas.models.forecast import run_forecast_backtest, saturday_as_of


def test_forecast_backtest_on_synthetic_panel() -> None:
    n = 220
    start = date(2020, 1, 3)  # Friday
    weeks = [start + timedelta(days=7 * i) for i in range(n)]
    rng = np.random.default_rng(0)
    hdd = rng.uniform(20, 200, n)
    panel = pd.DataFrame(
        {
            "week_ending": weeks,
            "storage_change_bcf": 40 - 0.3 * hdd + rng.normal(0, 5, n),
            "gw_hdd": hdd,
            "gw_cdd": rng.uniform(0, 40, n),
            "cpc_hdd": hdd,
            "cpc_cdd": rng.uniform(0, 40, n),
            "production_bcf_d": 90.0,
        }
    )

    obs_rows, fcst_rows = [], []
    regions = [
        "NEW ENGLAND",
        "EAST NORTH CENTRAL",
        "WEST NORTH CENTRAL",
        "SOUTH ATLANTIC",
        "EAST SOUTH CENTRAL",
        "WEST SOUTH CENTRAL",
        "MOUNTAIN",
        "PACIFIC",
        "NATIONAL",
    ]
    for we, h in zip(weeks, hdd):
        as_of = saturday_as_of(we)
        for i in range(7):
            d = as_of + timedelta(days=i)
            daily_h = float(h) / 7
            for r in regions:
                obs_rows.append({"date": d, "region": r, "hdd": daily_h, "cdd": 0.0})
                fcst_rows.append(
                    {
                        "issued": as_of,
                        "date": d,
                        "region": r,
                        "hdd": daily_h + 1.0,
                        "cdd": 0.0,
                        "lead_days": i,
                    }
                )
    obs = pd.DataFrame(obs_rows)
    fcst = pd.DataFrame(fcst_rows)
    result = run_forecast_backtest(panel, obs, fcst, as_of="saturday", n_splits=3, min_train=80)
    assert result.model_mae < 20
    assert np.isfinite(result.model_mae)
    assert {"interval_lower", "interval_upper"}.issubset(result.predictions.columns)
    assert (result.predictions["interval_upper"] >= result.predictions["interval_lower"]).all()
