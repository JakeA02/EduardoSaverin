"""Analyst consensus ingest. Never used as a model feature.

Operational source is Trading Economics' economist survey: it is public, has
no API key, and GitHub Actions can fetch it. Reuters/NGI/Bloomberg are
paywalled. Investing.com has a similar street forecast but blocks datacenter
IPs, so it is only used to fill weeks TE does not yet have.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests

from natgas.calendar import week_ending_friday
from natgas.config import CONSENSUS_DIR, RAW_DIR, ensure_dirs
from natgas.predictions import backfill_outcomes

TRACKED_CSV = CONSENSUS_DIR / "consensus.csv"
LEGACY_CSV = RAW_DIR / "consensus" / "consensus.csv"
CONSENSUS_TEMPLATE = TRACKED_CSV  # kept for callers that imported the old name

TE_URL = "https://tradingeconomics.com/united-states/natural-gas-stocks-change"
INVESTING_URL = "https://www.investing.com/economic-calendar/natural-gas-storage-386"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PROTECTED_SOURCES = {"manual"}
COLUMNS = ["week_ending", "consensus_median", "consensus_low", "consensus_high", "source"]

_BCF_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:billion cubic feet|bcf|b\b)",
    re.IGNORECASE,
)
_NEWS_WEEK_RE = re.compile(
    r"week end(?:ed|ing) ([A-Za-z]+ \d{1,2}).{0,120}?market expectations of (-?\d+(?:\.\d+)?)\s*bcf",
    re.IGNORECASE | re.DOTALL,
)
_NEWS_DATE_RE = re.compile(
    r'class="te-stream-date">\s*(\d{4}-\d{2}-\d{2})\s*<',
    re.IGNORECASE,
)


def parse_bcf(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = cleaned.replace(",", "").replace("\xa0", " ").strip()
    if not cleaned or cleaned in {".", "-"}:
        return None
    match = _BCF_RE.search(cleaned)
    if match:
        return float(match.group(1))
    return None


def _parse_month_day(token: str, year: int) -> date | None:
    token = token.strip().replace("-", "/")
    for fmt in ("%b/%d", "%B/%d", "%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(f"{token} {year}", f"{fmt} %Y").replace(tzinfo=UTC).date()
            return parsed
        except ValueError:
            continue
    return None


def _ref_to_week_ending(ref: str, release: date) -> date | None:
    parsed = _parse_month_day(ref, release.year)
    if parsed is None:
        return None
    if parsed > release:
        parsed = _parse_month_day(ref, release.year - 1) or parsed
    return week_ending_friday(parsed)


def release_to_week_ending(release: date) -> date:
    """EIA WNGSR Thursday maps to the prior Friday storage week."""
    if release.weekday() == 3:  # Thursday
        return release - timedelta(days=6)
    return week_ending_friday(release - timedelta(days=1))


class _TableParser(HTMLParser):
    """Collect text cells from HTML tables, ignoring nested scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._cell is None:
            return
        self._cell.append(data)


def parse_te_calendar(html: str) -> pd.DataFrame:
    parser = _TableParser()
    parser.feed(html)
    rows: list[dict] = []
    for table in parser.tables:
        if not table:
            continue
        header_blob = " ".join(table[0]).lower()
        if "consensus" not in header_blob:
            continue
        for cells in table[1:]:
            try:
                release = date.fromisoformat(cells[0][:10])
            except (ValueError, IndexError):
                continue
            # Reference is colspan=2 in the header, so body has an extra event-name cell.
            if len(cells) >= 8:
                ref, consensus_cell = cells[3], cells[6]
            elif len(cells) >= 7:
                ref, consensus_cell = cells[2], cells[5]
            else:
                continue
            consensus = parse_bcf(consensus_cell)
            if consensus is None:
                continue
            week = _ref_to_week_ending(ref, release) if ref else release_to_week_ending(release)
            if week is None:
                continue
            rows.append(
                {
                    "week_ending": week,
                    "consensus_median": consensus,
                    "consensus_low": None,
                    "consensus_high": None,
                    "source": "tradingeconomics",
                }
            )
    return _frame(rows)


def parse_te_news(html: str) -> pd.DataFrame:
    blocks = re.split(r'class="[^"]*te-stream-repeater[^"]*"', html)
    rows: list[dict] = []
    for block in blocks[1:]:
        date_match = _NEWS_DATE_RE.search(block)
        week_match = _NEWS_WEEK_RE.search(block)
        if not date_match or not week_match:
            continue
        article_date = date.fromisoformat(date_match.group(1))
        ref = _parse_month_day(week_match.group(1), article_date.year)
        if ref is None:
            continue
        if ref > article_date:
            ref = _parse_month_day(week_match.group(1), article_date.year - 1) or ref
        rows.append(
            {
                "week_ending": week_ending_friday(ref),
                "consensus_median": float(week_match.group(2)),
                "consensus_low": None,
                "consensus_high": None,
                "source": "tradingeconomics",
            }
        )
    return _frame(rows)


def parse_investing_history(html: str) -> pd.DataFrame:
    parser = _TableParser()
    parser.feed(html)
    rows: list[dict] = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        header = [re.sub(r"[^a-z]", "", c.lower()) for c in table[0]]
        if "forecast" not in header or "actual" not in header:
            continue
        col = {name: i for i, name in enumerate(header)}
        for cells in table[1:]:
            if len(cells) <= col["forecast"]:
                continue
            consensus = parse_bcf(cells[col["forecast"]])
            if consensus is None:
                continue
            release = _parse_investing_date(cells[0])
            if release is None:
                continue
            rows.append(
                {
                    "week_ending": release_to_week_ending(release),
                    "consensus_median": consensus,
                    "consensus_low": None,
                    "consensus_high": None,
                    "source": "investing",
                }
            )
    return _frame(rows)


def _parse_investing_date(text: str) -> date | None:
    text = text.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    df["week_ending"] = pd.to_datetime(df["week_ending"]).dt.date
    df["consensus_median"] = pd.to_numeric(df["consensus_median"], errors="coerce")
    df = df.dropna(subset=["week_ending", "consensus_median"])
    df = df.drop_duplicates(subset=["week_ending"], keep="first")
    return df[COLUMNS]


def _fetch(url: str, timeout: int = 45) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def fetch_consensus() -> pd.DataFrame:
    """Pull public survey estimates. TE is required-path; Investing.com is optional."""
    frames: list[pd.DataFrame] = []
    te_html = _fetch(TE_URL)
    if te_html:
        frames.append(parse_te_calendar(te_html))
        frames.append(parse_te_news(te_html))
    inv_html = _fetch(INVESTING_URL)
    if inv_html:
        frames.append(parse_investing_history(inv_html))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    # Prefer Trading Economics when both sources cover the same week.
    rank = combined["source"].map({"tradingeconomics": 0, "investing": 1}).fillna(2)
    combined = combined.assign(_rank=rank).sort_values(["week_ending", "_rank"])
    combined = combined.drop_duplicates(subset=["week_ending"], keep="first").drop(columns="_rank")
    return combined[COLUMNS]


def template_path() -> Path:
    ensure_dirs()
    TRACKED_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not TRACKED_CSV.exists():
        if LEGACY_CSV.exists():
            TRACKED_CSV.write_text(LEGACY_CSV.read_text())
        else:
            TRACKED_CSV.write_text(",".join(COLUMNS) + "\n")
    return TRACKED_CSV


def load_consensus() -> pd.DataFrame:
    path = template_path()
    df = pd.read_csv(path, comment="#")
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["week_ending"] = pd.to_datetime(df["week_ending"]).dt.date
    df["consensus_median"] = pd.to_numeric(df["consensus_median"], errors="coerce")
    return df.dropna(subset=["week_ending", "consensus_median"])


def _write_consensus(df: pd.DataFrame) -> Path:
    path = template_path()
    out = df.copy()
    out["week_ending"] = pd.to_datetime(out["week_ending"]).dt.strftime("%Y-%m-%d")
    for col in ("consensus_low", "consensus_high"):
        if col not in out.columns:
            out[col] = None
    out = out[COLUMNS].sort_values("week_ending")
    out.to_csv(path, index=False)
    save_snapshot(out)
    return path


def append_consensus(
    week_ending: str,
    median: float,
    *,
    low: float | None = None,
    high: float | None = None,
    source: str = "manual",
) -> Path:
    """Append a consensus row after the model prediction has already been logged."""
    df = load_consensus()
    we = pd.to_datetime(week_ending).date()
    df = df[df["week_ending"] != we]
    row = {
        "week_ending": we,
        "consensus_median": median,
        "consensus_low": low,
        "consensus_high": high,
        "source": source,
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    path = _write_consensus(df)
    backfill_outcomes(we.isoformat(), consensus_median=median)
    return path


def merge_fetched(existing: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """Insert/update automated rows without clobbering manual entries."""
    if fetched.empty:
        return existing.copy() if not existing.empty else _frame([])
    if existing.empty:
        return fetched.copy()
    existing = existing.copy()
    fetched = fetched.copy()
    existing["week_ending"] = pd.to_datetime(existing["week_ending"]).dt.date
    fetched["week_ending"] = pd.to_datetime(fetched["week_ending"]).dt.date
    protected_weeks = set(existing.loc[existing["source"].isin(PROTECTED_SOURCES), "week_ending"])
    fetched = fetched[~fetched["week_ending"].isin(protected_weeks)]
    keep = existing[~existing["week_ending"].isin(set(fetched["week_ending"]))]
    merged = pd.concat([keep, fetched], ignore_index=True)
    return merged.sort_values("week_ending").reset_index(drop=True)


def ingest(*, persist: bool = True) -> pd.DataFrame:
    fetched = fetch_consensus()
    existing = load_consensus() if TRACKED_CSV.exists() or LEGACY_CSV.exists() else _frame([])
    merged = merge_fetched(existing, fetched)
    if persist:
        _write_consensus(merged)
        from natgas.db import init_schema

        init_schema()
        for _, row in fetched.iterrows():
            backfill_outcomes(
                row["week_ending"].isoformat(),
                consensus_median=float(row["consensus_median"]),
            )
    return merged


def save_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "consensus" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "consensus.parquet"
    snap = df.copy()
    snap["week_ending"] = pd.to_datetime(snap["week_ending"])
    snap.to_parquet(path, index=False)
    return path
