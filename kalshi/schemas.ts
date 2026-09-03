import { z } from 'zod';
import type { NormalizedMarket } from '@/lib/markets/normalized';
import { quoteDollarsToCents, parseFpCount } from './prices';

export type { NormalizedMarket } from '@/lib/markets/normalized';

export const FixedPointDollars = z.union([z.string(), z.number()]);
export const FixedPointCount = z.union([z.string(), z.number()]);

export const KalshiMarketSchema = z
  .object({
    ticker: z.string(),
    event_ticker: z.string().optional(),
    title: z.string().optional(),
    subtitle: z.string().optional(),
    status: z.string().optional(),
    category: z.string().optional(),
    yes_bid_dollars: FixedPointDollars.optional().nullable(),
    yes_ask_dollars: FixedPointDollars.optional().nullable(),
    no_bid_dollars: FixedPointDollars.optional().nullable(),
    no_ask_dollars: FixedPointDollars.optional().nullable(),
    yes_bid_size_fp: FixedPointCount.optional().nullable(),
    yes_ask_size_fp: FixedPointCount.optional().nullable(),
    volume_fp: FixedPointCount.optional().nullable(),
    volume_24h_fp: FixedPointCount.optional().nullable(),
    open_interest_fp: FixedPointCount.optional().nullable(),
    close_time: z.string().optional().nullable(),
    expiration_time: z.string().optional().nullable(),
    rules_primary: z.string().optional().nullable(),
    rules_secondary: z.string().optional().nullable(),
    result: z.string().optional().nullable(),
  })
  .passthrough();

export type KalshiMarket = z.infer<typeof KalshiMarketSchema>;

export function normalizeMarket(raw: KalshiMarket): NormalizedMarket {
  const yesBid = quoteDollarsToCents(raw.yes_bid_dollars);
  const yesAsk = quoteDollarsToCents(raw.yes_ask_dollars);
  const noBid = quoteDollarsToCents(raw.no_bid_dollars);
  let noAsk = quoteDollarsToCents(raw.no_ask_dollars);
  // Complementary empty-book sentinel: NO ask "1.0000" with no YES bid is not a real quote
  if (noAsk === 100 && yesBid === null) noAsk = null;
  if (noAsk === null && yesBid !== null) noAsk = 100 - yesBid;

  // List endpoint top-of-book size — NOT aggregated orderbook depth.
  // Scan job re-checks via /orderbook for the shortlist.
  const noAskSize = parseFpCount(raw.yes_bid_size_fp);

  return {
    venue: 'kalshi',
    ticker: raw.ticker,
    eventTicker: raw.event_ticker ?? null,
    title: raw.title ?? raw.ticker,
    category: raw.category ?? null,
    yesBid,
    yesAsk,
    noBid,
    noAsk,
    yesAskSize: parseFpCount(raw.yes_ask_size_fp),
    noAskSize,
    volume: parseFpCount(raw.volume_fp),
    volume24h: parseFpCount(raw.volume_24h_fp),
    openInterest: parseFpCount(raw.open_interest_fp),
    closeTime: raw.close_time ?? raw.expiration_time ?? null,
    rulesPrimary: raw.rules_primary ?? '',
    rulesSecondary: raw.rules_secondary ?? '',
    result: raw.result ?? null,
    raw,
  };
}

export function assertDollarPriceFields(sample: Record<string, unknown>): void {
  const required = ['yes_bid_dollars', 'yes_ask_dollars'];
  for (const key of required) {
    if (!(key in sample)) {
      throw new Error(
        `Kalshi market response missing expected dollar price field "${key}". Confirm API field names.`,
      );
    }
  }

  // Units check: non-zero quotes must be dollar fractions (≤ 1.0), not legacy integer cents.
  for (const key of required) {
    const raw = sample[key];
    if (raw === null || raw === undefined || raw === '' || raw === '0' || raw === '0.0000') {
      continue;
    }
    const n = typeof raw === 'number' ? raw : Number(raw);
    if (!Number.isFinite(n)) continue;
    if (n > 1) {
      throw new Error(
        `Kalshi ${key}=${JSON.stringify(raw)} looks like integer cents, not dollars. Refusing to proceed.`,
      );
    }
  }
}

export const KalshiOrderSchema = z
  .object({
    order_id: z.string().optional(),
    client_order_id: z.string().optional(),
    ticker: z.string().optional(),
    status: z.string().optional(),
    side: z.string().optional(),
    action: z.string().optional(),
    yes_price_dollars: FixedPointDollars.optional().nullable(),
    no_price_dollars: FixedPointDollars.optional().nullable(),
    fill_count_fp: FixedPointCount.optional().nullable(),
    remaining_count_fp: FixedPointCount.optional().nullable(),
    initial_count_fp: FixedPointCount.optional().nullable(),
  })
  .passthrough();

export const KalshiFillSchema = z
  .object({
    fill_id: z.string().optional(),
    order_id: z.string().optional(),
    ticker: z.string().optional(),
    count_fp: FixedPointCount.optional().nullable(),
    yes_price_dollars: FixedPointDollars.optional().nullable(),
    no_price_dollars: FixedPointDollars.optional().nullable(),
    fee_cost: FixedPointDollars.optional().nullable(),
    created_time: z.string().optional().nullable(),
  })
  .passthrough();
