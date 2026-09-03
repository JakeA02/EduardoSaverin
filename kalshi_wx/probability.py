"""Maps a WeatherNext point forecast onto Kalshi contract probabilities."""

from __future__ import annotations

import math
from dataclasses import dataclass

from kalshi_wx.config import (
    QPF_SIGMA_BASE_IN,
    QPF_SIGMA_PER_LEAD_IN,
    TAKER_FEE_COEFF,
    TMAX_SIGMA_BASE_F,
    TMAX_SIGMA_MAX_F,
    TMAX_SIGMA_MIN_F,
    TMAX_SIGMA_PER_LEAD_F,
)


def norm_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def clip01(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, float(p)))


def tmax_sigma_f(lead_days: float) -> float:
    sigma = TMAX_SIGMA_BASE_F + TMAX_SIGMA_PER_LEAD_F * max(0.0, lead_days)
    return min(TMAX_SIGMA_MAX_F, max(TMAX_SIGMA_MIN_F, sigma))


def p_integer_tmax(mu: float, sigma: float, lo: int, hi: int) -> float:
    """P(rounded Tmax ∈ {lo, …, hi}) with continuity correction."""
    if hi < lo:
        return 0.0
    return clip01(norm_cdf(hi + 0.5, mu, sigma) - norm_cdf(lo - 0.5, mu, sigma))


def p_tmax_less_than(mu: float, sigma: float, strike: int) -> float:
    """YES iff integer max ≤ strike − 1 (Kalshi less-than threshold)."""
    return clip01(norm_cdf(strike - 1 + 0.5, mu, sigma))


def p_tmax_greater_than(mu: float, sigma: float, strike: int) -> float:
    """YES iff integer max ≥ strike + 1 (Kalshi greater-than threshold)."""
    return clip01(1.0 - norm_cdf(strike + 1 - 0.5, mu, sigma))


def qpf_sigma_in(lead_days: float) -> float:
    return QPF_SIGMA_BASE_IN + QPF_SIGMA_PER_LEAD_IN * max(0.0, lead_days)


def p_rain_range(daily: list[tuple[float, list[float], float]]) -> float:
    """P(at least one day rains) from (qpf_in, hourly_pops, lead_days) tuples."""
    prod = 1.0
    for qpf_in, pops, lead in daily:
        prod *= 1.0 - p_rain(qpf_in, pops, lead)
    return clip01(1.0 - prod)


def p_consecutive_ge(day_probs: list[float], k: int) -> float:
    """P(a run of at least k independent Bernoulli successes)."""
    if k <= 0:
        return 1.0
    n = len(day_probs)
    if k > n:
        return 0.0
    # dp[j] = P(current streak j, have not yet hit k). dp[k] = already succeeded.
    dp = [0.0] * (k + 1)
    dp[0] = 1.0
    for p in day_probs:
        p = min(1.0, max(0.0, float(p)))
        nxt = [0.0] * (k + 1)
        nxt[k] = dp[k]
        for j in range(k):
            nxt[0] += dp[j] * (1.0 - p)
            nxt[k if j + 1 >= k else j + 1] += dp[j] * p
        dp = nxt
    return clip01(dp[k])


def p_day_above(tmean_f: float, sigma: float, threshold_f: float = 90.0) -> float:
    return clip01(1.0 - norm_cdf(threshold_f - 0.5, tmean_f, sigma))


def p_rain(qpf_in: float, hourly_pops: list[float], lead_days: float) -> float:
    """Blend QPF mass above a trace with a dependence-damped hourly PoP."""
    sigma = qpf_sigma_in(lead_days)
    p_qpf = 1.0 - norm_cdf(0.005, qpf_in, sigma)
    prod = 1.0
    for p in hourly_pops:
        prod *= 1.0 - min(0.99, max(0.0, p))
    p_union = 1.0 - prod
    # Hourly PoPs are not independent; shrink toward the max hour.
    p_max = max(hourly_pops) if hourly_pops else 0.0
    p_pop = 0.5 * p_union + 0.5 * p_max
    return clip01(0.55 * p_qpf + 0.45 * p_pop)


def taker_fee(price: float) -> float:
    p = min(0.99, max(0.01, float(price)))
    return TAKER_FEE_COEFF * p * (1.0 - p)


@dataclass(frozen=True)
class Edge:
    side: str  # yes | no | skip
    entry: float
    ev: float
    fee: float
    model_p: float
    mid: float | None


def contract_edge(
    model_p: float,
    yes_bid: float | None,
    yes_ask: float | None,
    *,
    min_ev: float,
) -> Edge:
    """Buy YES at ask or NO at (1 − bid) when EV clears min_ev after fees."""
    p = clip01(model_p)
    mid = None
    if yes_bid is not None and yes_ask is not None:
        mid = 0.5 * (yes_bid + yes_ask)
    elif yes_ask is not None:
        mid = yes_ask
    elif yes_bid is not None:
        mid = yes_bid

    best = Edge(side="skip", entry=0.0, ev=0.0, fee=0.0, model_p=p, mid=mid)

    if yes_ask is not None and 0.01 <= yes_ask <= 0.99:
        fee = taker_fee(yes_ask)
        ev = p - yes_ask - fee
        if ev >= min_ev and ev > best.ev:
            best = Edge(side="yes", entry=yes_ask, ev=ev, fee=fee, model_p=p, mid=mid)

    no_ask = None if yes_bid is None else 1.0 - yes_bid
    if no_ask is not None and 0.01 <= no_ask <= 0.99:
        fee = taker_fee(no_ask)
        ev = (1.0 - p) - no_ask - fee
        if ev >= min_ev and ev > best.ev:
            best = Edge(side="no", entry=no_ask, ev=ev, fee=fee, model_p=p, mid=mid)

    return best
