"""Storage surprise → directional QG paper trade.

Written rules (do not override discretionarily):

- Instrument: CME E-mini NG (QG). $1/MMBtu move = $2,500 per contract.
- Signal: predicted_surprise = model_prediction − consensus.
  More injection than the street (positive surprise) is bearish for price.
  Less injection than the street (negative surprise) is bullish.
- Deadband: |predicted_surprise| ≤ 5 Bcf → no trade. Large surprises are
  where the money is; small ones are noise.
- Size: 1 QG if 5 < |s| ≤ 15; 2 QG if |s| > 15. Cap 2. Never NG full-size.
- Entry: last daily close on or before the Wednesday before the EIA print.
- Exit: Thursday close on release day (after 10:30 ET print).
- Commission: $5 round-trip per contract (IBKR-ish).
- Stand-down: skip new entries after 4 consecutive settled losing weeks.
  Settling already-open trades is still allowed.
- Consensus never feeds the storage model. It only sizes this overlay.
"""

from __future__ import annotations

from dataclasses import dataclass

DEADBAND_BCF = 5.0
LARGE_SURPRISE_BCF = 15.0
MAX_CONTRACTS = 2
QG_DOLLARS_PER_POINT = 2500.0
COMMISSION_ROUND_TRIP = 5.0
MAX_CONSECUTIVE_LOSSES = 4
STRATEGY = "surprise-qg-v0.1"
INSTRUMENT = "QG"


@dataclass(frozen=True)
class TradeSignal:
    strategy: str
    instrument: str
    side: str  # long | short | flat
    n_contracts: int
    predicted_surprise: float
    size_reason: str


def predicted_surprise(predicted: float, consensus: float) -> float:
    return float(predicted) - float(consensus)


def size_contracts(abs_surprise: float) -> int:
    if abs_surprise <= DEADBAND_BCF:
        return 0
    if abs_surprise <= LARGE_SURPRISE_BCF:
        return 1
    return MAX_CONTRACTS


def build_signal(predicted: float, consensus: float) -> TradeSignal:
    surprise = predicted_surprise(predicted, consensus)
    n = size_contracts(abs(surprise))
    if n == 0:
        return TradeSignal(
            strategy=STRATEGY,
            instrument=INSTRUMENT,
            side="flat",
            n_contracts=0,
            predicted_surprise=surprise,
            size_reason=f"|surprise| {abs(surprise):.1f} Bcf ≤ {DEADBAND_BCF:.0f} deadband",
        )
    side = "short" if surprise > 0 else "long"
    band = "large" if n == MAX_CONTRACTS else "standard"
    return TradeSignal(
        strategy=STRATEGY,
        instrument=INSTRUMENT,
        side=side,
        n_contracts=n,
        predicted_surprise=surprise,
        size_reason=f"{band} {side} {n} {INSTRUMENT} on {surprise:+.1f} Bcf predicted surprise",
    )


def pnl_usd(
    side: str,
    n_contracts: int,
    entry_price: float,
    exit_price: float,
    *,
    commission: float = COMMISSION_ROUND_TRIP,
) -> float:
    if n_contracts <= 0 or side == "flat":
        return 0.0
    direction = 1.0 if side == "long" else -1.0
    move = direction * (exit_price - entry_price) * n_contracts * QG_DOLLARS_PER_POINT
    return round(move - commission * n_contracts, 2)


def reasoning_validated(
    predicted_surprise_bcf: float,
    actual_surprise_bcf: float,
) -> bool | None:
    """Did the print land on the same side of consensus as the model?"""
    if predicted_surprise_bcf == 0 or actual_surprise_bcf == 0:
        return None
    return (predicted_surprise_bcf > 0) == (actual_surprise_bcf > 0)
