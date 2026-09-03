/**
 * Expected fee formula for drift detection only.
 * published taker: 0.07 × P × (1 − P) per contract, P in [0,1].
 * Stored as integer cents (rounded to nearest cent).
 */
export function expectedFeeCentsPerContract(priceCents: number): number {
  const p = priceCents / 100;
  const feeDollars = 0.07 * p * (1 - p);
  return Math.round(feeDollars * 100);
}

export function expectedFeeCentsTotal(priceCents: number, count: number): number {
  return expectedFeeCentsPerContract(priceCents) * count;
}

export function feeDriftRatio(realizedCents: number, expectedCents: number): number | null {
  if (expectedCents === 0) return realizedCents === 0 ? 0 : null;
  return Math.abs(realizedCents - expectedCents) / expectedCents;
}

export function hasFeeDrift(realizedCents: number, expectedCents: number, threshold = 0.2): boolean {
  const ratio = feeDriftRatio(realizedCents, expectedCents);
  if (ratio === null) return realizedCents > 0;
  return ratio > threshold;
}
