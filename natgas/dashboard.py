"""Static dashboard for predictions vs consensus vs EIA actuals."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from natgas.calendar import current_target_week, next_target_week
from natgas.config import DASHBOARD_DIR, ensure_dirs
from natgas.ingest.consensus import load_consensus
from natgas.models.consensus_eval import ConsensusReport, evaluate_vs_consensus
from natgas.predictions import load_json_log, load_json_log_by_source

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Natgas storage forecast</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap" rel="stylesheet" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #0c1117;
      --panel: #141b24;
      --line: #243044;
      --text: #e8eef6;
      --muted: #8b9bb0;
      --accent: #6ee7b7;
      --warn: #fbbf24;
      --bad: #f87171;
      --model: #7dd3fc;
      --cons: #fbbf24;
      --act: #e8eef6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--text);
      font-family: "IBM Plex Sans", system-ui, sans-serif;
    }
    .site-nav {
      display: flex; gap: 4px; padding: 10px 32px 0;
      border-bottom: 1px solid var(--line);
    }
    .site-nav a {
      color: var(--muted); text-decoration: none; font-size: 12px;
      letter-spacing: 0.06em; text-transform: uppercase; padding: 8px 10px 10px;
    }
    .site-nav a.active { color: var(--text); border-bottom: 2px solid var(--accent); }
    .site-nav a:hover { color: var(--text); }
    header { padding: 22px 32px 8px; }
    header h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: -0.02em; }
    header p { margin: 0; color: var(--muted); font-size: 13px; }
    main { padding: 24px 32px 48px; max-width: 1200px; }
    .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .card {
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px;
    }
    .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; }
    .card .value { font-family: "IBM Plex Mono", monospace; font-size: 28px; margin-top: 6px; }
    .card .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .pass { color: var(--accent); }
    .fail { color: var(--bad); }
    .wait { color: var(--warn); }
    .grid { display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } main, header, .site-nav { padding-left: 16px; padding-right: 16px; } }
    canvas { width: 100%; height: 340px !important; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: var(--muted); font-weight: 500; padding: 8px 6px; border-bottom: 1px solid var(--line); }
    td { padding: 8px 6px; border-bottom: 1px solid var(--line); font-family: "IBM Plex Mono", monospace; }
    .note { color: var(--muted); font-size: 12px; margin-top: 18px; line-height: 1.5; }
  </style>
</head>
<body>
  <nav class="site-nav">
    <a class="active" href="./">Storage</a>
    <a href="./kalshi/">Weather markets</a>
    <a href="./bot/">Trading bot</a>
  </nav>
  <header>
    <h1>US natural gas storage forecast</h1>
    <p>Model vs Trading Economics survey consensus vs EIA actual · updated <span id="updated"></span></p>
  </header>
  <main>
    <section class="kpis" id="kpis"></section>
    <section class="grid">
      <div class="card"><canvas id="series"></canvas></div>
      <div class="card">
        <div class="label">Phase 4 gate</div>
        <div id="gate" class="value wait" style="font-size:22px;margin:10px 0 12px">—</div>
        <div class="sub" id="gate-reason"></div>
      </div>
    </section>
    <div class="card" style="margin-top:16px; overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Week ending</th><th>Model</th><th>WN3</th><th>Consensus</th><th>Actual</th>
            <th>Model err</th><th>Cons err</th><th>Surprise</th><th>Source</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="card" style="margin-top:16px; overflow-x:auto">
      <div class="label" style="margin-bottom:8px">Paper trades (QG, Wed close → Thu close)</div>
      <table>
        <thead>
          <tr>
            <th>Week</th><th>Status</th><th>Side</th><th>n</th>
            <th>Pred surprise</th><th>Actual surprise</th>
            <th>Entry</th><th>Exit</th><th>P&L</th><th>Reasoning</th>
          </tr>
        </thead>
        <tbody id="paper-rows"></tbody>
      </table>
    </div>
    <p class="note">
      Consensus is logged independently of the model and never used as a feature.
      Street surveys (Reuters/NGI) are paywalled; the automated series is the
      Trading Economics economist survey, ingested on GitHub Actions.
      EIA actuals overlay the official Thursday 10:30 ET WNGSR print
      (<code>ir.eia.gov/ngs/wngsr.json</code>) so a lagging Open Data API
      cannot leave the latest week blank. The gate requires 26 overlapping
      weeks with rolling MAE below consensus and surprise-direction hit rate
      above 50%. Paper trading is a QG overlay on predicted surprise versus
      consensus; it does not wait for the gate.
      WeatherNext 3 is an A/B forecast source (purple) and does not drive paper
      trades. Next-week predictions require 7 complete forecast days — Maps
      Weather API is 10 days, so week+1 is usually available from Wednesday;
      full WeatherNext 3 (15 days) covers it from Saturday.
      Rules are in <code>natgas/signal/translate.py</code>. Ledger:
      <code>data/paper/trades.csv</code>.
    </p>
  </main>
  <script>
    const payload = __PAYLOAD__;
    const fmt = (v, d=1) => v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d);
    const sign = (v) => v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(1);
    const money = (v) => v == null || Number.isNaN(v) ? "—" : (v < 0 ? "-$" : "$") + Math.abs(Number(v)).toFixed(0);
    const paper = payload.paper || {};
    const paperBook = (p) => {
      if (p.latest_open && p.latest_open.side && p.latest_open.side !== "flat") {
        return (p.latest_open.side + " " + p.latest_open.n_contracts + " QG").toUpperCase();
      }
      return (p.n_open ? p.n_open + " open" : "flat");
    };
    document.getElementById("updated").textContent = payload.generated_at || "n/a";

    const latest = payload.latest || {};
    const nextWeek = payload.next_week || {};
    const gate = payload.gate || {};
    const kpis = [
      ["This week (model)", sign(latest.predicted), latest.week_ending || ""],
      ["Next week", sign(nextWeek.predicted), nextWeek.week_ending ? ((nextWeek.nwp_source || "WN3") + " · " + nextWeek.week_ending) : "need 7 forecast days"],
      ["Consensus", sign(latest.consensus), latest.source || ""],
      ["EIA actual", sign(latest.actual), latest.actual == null ? "awaiting Thursday print" : "Bcf change"],
      ["Model − cons", sign(latest.model_minus_consensus), "Bcf"],
      ["Paper P&L", money(paper.cumulative_pnl_usd), paper.n_settled ? (paper.n_settled + " settled weeks") : "no fills yet"],
      ["Paper book", paperBook(paper), paper.strategy || "surprise-qg-v0.1"],
    ];
    document.getElementById("kpis").innerHTML = kpis.map(([label, value, sub]) =>
      `<div class="card"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`
    ).join("");

    const gateEl = document.getElementById("gate");
    const status = gate.gate_pass === true ? "PASS" : gate.gate_pass === false ? "FAIL" : "INCOMPLETE";
    gateEl.textContent = status;
    gateEl.className = "value " + (status === "PASS" ? "pass" : status === "FAIL" ? "fail" : "wait");
    document.getElementById("gate-reason").innerHTML = [
      gate.gate_reason || "",
      `n=${gate.n_weeks ?? 0} weeks · model MAE ${fmt(gate.model_mae)} · consensus MAE ${fmt(gate.consensus_mae)}`,
      `surprise hit ${(gate.surprise_hit_rate == null || Number.isNaN(gate.surprise_hit_rate)) ? "—" : (100*gate.surprise_hit_rate).toFixed(0) + "%"}`,
    ].join("<br/>");

    const series = payload.series || [];
    const labels = series.map(r => r.week_ending);
    new Chart(document.getElementById("series"), {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "EIA actual", data: series.map(r => r.actual), borderColor: "#e8eef6", tension: 0.15, spanGaps: true },
          { label: "Model (CPC)", data: series.map(r => r.predicted), borderColor: "#7dd3fc", tension: 0.15, spanGaps: true },
          { label: "WeatherNext 3", data: series.map(r => r.predicted_wn3), borderColor: "#c4b5fd", tension: 0.15, spanGaps: true },
          { label: "Consensus", data: series.map(r => r.consensus), borderColor: "#fbbf24", borderDash: [5,4], tension: 0.15, spanGaps: true },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#8b9bb0" } } },
        scales: {
          x: { ticks: { color: "#8b9bb0", maxTicksLimit: 10 }, grid: { color: "#243044" } },
          y: { ticks: { color: "#8b9bb0" }, grid: { color: "#243044" }, title: { display: true, text: "Bcf", color: "#8b9bb0" } }
        }
      }
    });

    document.getElementById("rows").innerHTML = series.slice().reverse().map(r => {
      const mErr = (r.predicted != null && r.actual != null) ? Math.abs(r.predicted - r.actual) : null;
      const cErr = (r.consensus != null && r.actual != null) ? Math.abs(r.consensus - r.actual) : null;
      const surprise = (r.actual != null && r.consensus != null) ? r.actual - r.consensus : null;
      return `<tr>
        <td>${r.week_ending}</td><td>${sign(r.predicted)}</td><td>${sign(r.predicted_wn3)}</td>
        <td>${sign(r.consensus)}</td>
        <td>${sign(r.actual)}</td><td>${fmt(mErr)}</td><td>${fmt(cErr)}</td>
        <td>${sign(surprise)}</td><td>${r.source || ""}</td>
      </tr>`;
    }).join("");

    const paperRows = (paper.trades || []).slice().reverse();
    document.getElementById("paper-rows").innerHTML = paperRows.map(r => {
      const reason = r.reasoning_ok == null ? "—" : (r.reasoning_ok ? "held" : "failed");
      return `<tr>
        <td>${r.week_ending}</td><td>${r.status}</td>
        <td>${r.side}</td><td>${r.n_contracts}</td>
        <td>${sign(r.predicted_surprise)}</td><td>${sign(r.actual_surprise)}</td>
        <td>${fmt(r.entry_price, 3)}</td><td>${fmt(r.exit_price, 3)}</td>
        <td>${money(r.pnl_usd)}</td><td>${reason}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="10">No paper rows yet — waiting for consensus + a model log.</td></tr>`;
  </script>
</body>
</html>
"""


def load_actuals() -> pd.DataFrame:
    """EIA weekly storage change from vintage, tracked CSV, or live WNGSR."""
    from natgas.ingest.eia import load_actuals as load_eia_actuals

    return load_eia_actuals()


def build_payload() -> dict:
    preds = load_json_log()
    by_source = load_json_log_by_source()
    consensus = load_consensus()
    actuals = load_actuals()

    wn3_map = {}
    if not by_source.empty:
        wn3 = by_source[by_source["nwp_source"] == "weathernext3"]
        if not wn3.empty:
            wn3_map = (
                wn3.sort_values("ts_utc")
                .groupby("target_week_ending", as_index=False)
                .tail(1)
                .set_index("target_week_ending")["predicted_change_bcf"]
                .to_dict()
            )

    weeks = set()
    if not preds.empty:
        weeks.update(preds["target_week_ending"])
    if not consensus.empty:
        weeks.update(consensus["week_ending"])
    weeks.update(wn3_map.keys())

    cons_map = (
        consensus.set_index("week_ending")[["consensus_median", "source"]]
        if not consensus.empty
        else None
    )
    pred_map = (
        preds.set_index("target_week_ending")
        if not preds.empty
        else None
    )
    act_map = actuals.set_index("week_ending")["storage_change_bcf"] if not actuals.empty else None

    series = []
    for week in sorted(weeks):
        pred_row = pred_map.loc[week] if pred_map is not None and week in pred_map.index else None
        cons_row = cons_map.loc[week] if cons_map is not None and week in cons_map.index else None
        predicted = None if pred_row is None else float(pred_row["predicted_change_bcf"])
        consensus_v = None if cons_row is None else float(cons_row["consensus_median"])
        actual = None if act_map is None or week not in act_map.index else float(act_map.loc[week])
        source = None if cons_row is None else str(cons_row["source"])
        wn3 = None if week not in wn3_map else float(wn3_map[week])
        series.append(
            {
                "week_ending": week.isoformat(),
                "predicted": predicted,
                "predicted_wn3": wn3,
                "consensus": consensus_v,
                "actual": actual,
                "source": source,
                "model_version": None if pred_row is None else pred_row.get("model_version"),
                "nwp_source": None if pred_row is None else pred_row.get("nwp_source"),
            }
        )

    report: ConsensusReport | None = None
    if not preds.empty and not consensus.empty and not actuals.empty:
        pred_df = preds.rename(
            columns={"target_week_ending": "week_ending", "predicted_change_bcf": "predicted"}
        )
        pred_df = pred_df.merge(actuals, on="week_ending", how="inner")
        if not pred_df.empty:
            report = evaluate_vs_consensus(pred_df, consensus)

    this_week = current_target_week()
    following = next_target_week()
    by_week = {row["week_ending"]: row for row in series}

    def _week_payload(week: date) -> dict:
        row = by_week.get(week.isoformat(), {})
        predicted = row.get("predicted")
        if predicted is None:
            predicted = row.get("predicted_wn3")
        return {
            **row,
            "week_ending": week.isoformat(),
            "predicted": predicted,
            "nwp_source": row.get("nwp_source"),
            "model_minus_consensus": (
                None
                if predicted is None or row.get("consensus") is None
                else predicted - row["consensus"]
            ),
        }

    latest = _week_payload(this_week)
    next_week = _week_payload(following)

    gate = {
        "n_weeks": 0 if report is None else report.n_weeks,
        "model_mae": None if report is None else report.model_mae,
        "consensus_mae": None if report is None else report.consensus_mae,
        "rolling_26_model_mae": None if report is None else report.rolling_26_model_mae,
        "rolling_26_consensus_mae": None if report is None else report.rolling_26_consensus_mae,
        "surprise_hit_rate": None if report is None else report.surprise_hit_rate,
        "gate_pass": None if report is None else report.gate_pass,
        "gate_reason": (
            "Waiting for overlapping model predictions, consensus, and EIA actuals."
            if report is None
            else report.gate_reason
        ),
    }

    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "latest": latest,
        "next_week": next_week,
        "gate": gate,
        "series": series[-52:],
        "n_consensus_weeks": 0 if consensus.empty else len(consensus),
        "n_predictions": 0 if preds.empty else len(preds),
        "paper": _paper_payload(),
    }


def _paper_payload() -> dict:
    try:
        from natgas.signal.paper import paper_summary

        return paper_summary()
    except (OSError, ValueError):
        return {
            "strategy": "surprise-qg-v0.1",
            "n_settled": 0,
            "n_open": 0,
            "n_skipped": 0,
            "cumulative_pnl_usd": 0.0,
            "win_rate": None,
            "reasoning_hit_rate": None,
            "consecutive_losses": 0,
            "latest_open": None,
            "trades": [],
        }


def generate(output_dir: Path | None = None) -> Path:
    ensure_dirs()
    out = output_dir or DASHBOARD_DIR
    out.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    (out / "data.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    html = _DASHBOARD_HTML.replace("__PAYLOAD__", json.dumps(payload, default=str))
    (out / "index.html").write_text(html)
    (out / ".nojekyll").write_text("")
    return out / "index.html"
