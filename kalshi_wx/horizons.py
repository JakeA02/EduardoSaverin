"""Paper-horizon policies. Near (D+1) and far (D+2–10) are scored separately."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kalshi_wx.config import (
    FAR_MAX_LEAD_DAYS,
    FAR_MIN_LEAD_DAYS,
    FAR_MIN_VOLUME,
    MAX_MID,
    MAX_PAPER_PER_SCAN,
    MIN_EV,
    MIN_MID,
    MIN_VOLUME,
    NEAR_LEAD_DAYS,
)


@dataclass(frozen=True)
class HorizonPolicy:
    """One independent paper book. Windows do not overlap."""

    id: str
    label: str
    min_lead_days: int
    max_lead_days: int
    min_volume: float
    min_mid: float = MIN_MID
    max_mid: float = MAX_MID
    min_ev: float = MIN_EV
    max_tickets: int = MAX_PAPER_PER_SCAN
    kinds: tuple[str, ...] = ("high", "rain", "rain_weekend", "weekly_hot")
    legacy_ids: tuple[str, ...] = ()

    def matches_lead(self, lead_days: int) -> bool:
        return self.min_lead_days <= lead_days <= self.max_lead_days

    def aliases(self) -> frozenset[str]:
        return frozenset((self.id, *self.legacy_ids))


NEAR = HorizonPolicy(
    id="near",
    label="D+1",
    min_lead_days=NEAR_LEAD_DAYS,
    max_lead_days=NEAR_LEAD_DAYS,
    min_volume=MIN_VOLUME,
    kinds=("high", "rain"),
    legacy_ids=("wx-kalshi-v0.1",),
)

FAR = HorizonPolicy(
    id="far",
    label="D+2–10",
    min_lead_days=FAR_MIN_LEAD_DAYS,
    max_lead_days=FAR_MAX_LEAD_DAYS,
    min_volume=FAR_MIN_VOLUME,
    kinds=("high", "rain", "rain_weekend", "weekly_hot"),
)

STRATEGIES: tuple[HorizonPolicy, ...] = (NEAR, FAR)


def calendar_lead(target: date, today: date) -> int:
    return (target - today).days


def policy_by_id(strategy_id: str) -> HorizonPolicy | None:
    for policy in STRATEGIES:
        if strategy_id in policy.aliases():
            return policy
    return None


def assign_strategies(lead_days: int, kind: str) -> list[str]:
    return [
        p.id
        for p in STRATEGIES
        if p.matches_lead(lead_days) and kind in p.kinds
    ]


def book_ok(row: dict, policy: HorizonPolicy) -> bool:
    if row.get("side") in (None, "skip"):
        return False
    kind = row.get("kind") or ""
    if kind and kind not in policy.kinds:
        return False
    mid = row.get("mid")
    if mid is None or mid < policy.min_mid or mid > policy.max_mid:
        return False
    if row.get("yes_bid") is None or row.get("yes_ask") is None:
        return False
    if float(row.get("volume") or 0) < policy.min_volume:
        return False
    if float(row.get("ev") or 0) < policy.min_ev:
        return False
    return True


def eligible_for(row: dict, policy: HorizonPolicy, *, today: date) -> bool:
    raw_target = row.get("target_date")
    try:
        target = raw_target if isinstance(raw_target, date) else date.fromisoformat(str(raw_target or ""))
    except ValueError:
        return False
    lead = calendar_lead(target, today)
    if not policy.matches_lead(lead):
        return False
    return book_ok(row, policy)


def tag_candidate(row: dict, *, today: date) -> dict:
    """Attach calendar lead + per-strategy eligibility. Mutates and returns row."""
    try:
        target = date.fromisoformat(str(row.get("target_date") or ""))
        lead = calendar_lead(target, today)
    except ValueError:
        lead = -1
    row["calendar_lead"] = lead
    eligible: dict[str, bool] = {}
    for policy in STRATEGIES:
        eligible[policy.id] = eligible_for(row, policy, today=today)
    row["eligible"] = eligible
    row["strategies"] = [sid for sid, ok in eligible.items() if ok]
    row["paper_eligible"] = bool(eligible.get(NEAR.id))
    row["paper_eligible_far"] = bool(eligible.get(FAR.id))
    return row
