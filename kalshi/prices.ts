/**
 * Kalshi API uses fixed-point dollar strings (e.g. "0.1200").
 * Internally we store integer cents (1–99) per the agent spec.
 */

export function dollarsToCents(dollars: string | number | null | undefined): number | null {
  if (dollars === null || dollars === undefined || dollars === '') return null;
  const n = typeof dollars === 'number' ? dollars : Number(dollars);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

/**
 * Top-of-book quotes: Kalshi returns "0.0000" (not null) when a side is empty.
 * Treat zero as missing so screen filters don't confuse unquoted with 0¢.
 */
export function quoteDollarsToCents(
  dollars: string | number | null | undefined,
): number | null {
  const cents = dollarsToCents(dollars);
  if (cents === null || cents === 0) return null;
  return cents;
}

export function centsToDollars(cents: number): string {
  return (cents / 100).toFixed(4);
}

export function parseFpCount(fp: string | number | null | undefined): number {
  if (fp === null || fp === undefined || fp === '') return 0;
  const n = typeof fp === 'number' ? fp : Number(fp);
  if (!Number.isFinite(n)) return 0;
  return Math.floor(n);
}

/** Mid price in cents from yes bid/ask. */
export function midCents(yesBid: number | null, yesAsk: number | null): number | null {
  if (yesBid === null || yesAsk === null) return null;
  return Math.round((yesBid + yesAsk) / 2);
}

/** NO ask ≈ 100 - YES bid (when YES bid exists). Prefer explicit no_ask when present. */
export function noAskFromYes(yesBidCents: number | null, noAskCents: number | null): number | null {
  if (noAskCents !== null) return noAskCents;
  if (yesBidCents === null) return null;
  return 100 - yesBidCents;
}
