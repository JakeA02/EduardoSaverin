"""Static Kalshi weather dashboard → dashboard/kalshi/ (Vercel /kalshi/)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kalshi_wx.config import DASHBOARD_DIR, MODEL_VERSION, ensure_dirs
from kalshi_wx.horizons import FAR, NEAR
from kalshi_wx.paper import paper_summary
from kalshi_wx.scan import load_latest_scan, load_scan_history

_TEMPLATE = Path(__file__).resolve().parent / "static" / "dashboard.html"


def build_payload(scan: dict | None = None) -> dict:
    scan = scan or load_latest_scan()
    paper = paper_summary()
    history = load_scan_history(limit=16)
    if not scan:
        return {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "model_version": MODEL_VERSION,
            "strategy": f"{NEAR.id}+{FAR.id}",
            "ts_utc": None,
            "n_markets": 0,
            "n_high": 0,
            "n_rain": 0,
            "n_weekend": 0,
            "n_weekly": 0,
            "n_tradeable": 0,
            "n_tradeable_near": 0,
            "n_tradeable_far": 0,
            "best_ev": None,
            "best_ev_near": None,
            "best_ev_far": None,
            "candidates": [],
            "forecasts": [],
            "history": [],
            "paper": paper,
        }
    candidates = scan.get("candidates") or []
    near = [c for c in candidates if (c.get("eligible") or {}).get(NEAR.id) or c.get("paper_eligible")]
    far = [c for c in candidates if (c.get("eligible") or {}).get(FAR.id) or c.get("paper_eligible_far")]
    best = max((c.get("ev") or 0) for c in near) if near else None
    best_far = max((c.get("ev") or 0) for c in far) if far else None
    hist_rows = []
    for rec in history:
        hist_rows.append(
            {
                "ts_utc": rec.get("ts_utc"),
                "n_markets": rec.get("n_markets"),
                "n_tradeable": rec.get("n_tradeable"),
                "n_tradeable_near": rec.get("n_tradeable_near", rec.get("n_tradeable")),
                "n_tradeable_far": rec.get("n_tradeable_far", 0),
            }
        )
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "model_version": scan.get("model_version") or MODEL_VERSION,
        "strategy": f"{NEAR.id}+{FAR.id}",
        "ts_utc": scan.get("ts_utc"),
        "n_markets": scan.get("n_markets") or len(candidates),
        "n_high": scan.get("n_high") or 0,
        "n_rain": scan.get("n_rain") or 0,
        "n_weekend": scan.get("n_weekend") or 0,
        "n_weekly": scan.get("n_weekly") or 0,
        "n_tradeable": scan.get("n_tradeable") or len(near),
        "n_tradeable_near": scan.get("n_tradeable_near") or len(near),
        "n_tradeable_far": scan.get("n_tradeable_far") or len(far),
        "best_ev": best,
        "best_ev_near": best,
        "best_ev_far": best_far,
        "candidates": candidates,
        "forecasts": scan.get("forecasts") or [],
        "history": hist_rows,
        "paper": paper,
    }


def generate(output_dir: Path | None = None, scan: dict | None = None) -> Path:
    ensure_dirs()
    out = output_dir or DASHBOARD_DIR
    out.mkdir(parents=True, exist_ok=True)
    payload = build_payload(scan)
    html = _TEMPLATE.read_text().replace("__PAYLOAD__", json.dumps(payload, default=str))
    (out / "data.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    (out / "index.html").write_text(html)
    return out / "index.html"
