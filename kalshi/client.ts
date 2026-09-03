import { getEnv, requireEnv } from '@/lib/env';
import { getKalshiPrivateKey } from './keys';
import { authHeaders } from './sign';
import {
  assertDollarPriceFields,
  KalshiFillSchema,
  KalshiMarketSchema,
  KalshiOrderSchema,
  normalizeMarket,
  type NormalizedMarket,
} from './schemas';
import {
  acquirePermit,
  backoffDelayMs,
  incrementHttp429,
  setRateLimits,
  sleep,
} from './rate-limit';
import { centsToDollars } from './prices';
import { z } from 'zod';

export type KalshiEnv = 'demo' | 'production';

export class KalshiHttpError extends Error {
  constructor(
    message: string,
    public status: number,
    public body: string,
    public ambiguous: boolean,
  ) {
    super(message);
    this.name = 'KalshiHttpError';
  }
}

type RequestOpts = {
  method?: string;
  body?: unknown;
  auth?: boolean;
  env?: KalshiEnv;
  isWrite?: boolean;
  maxAttempts?: number;
};

function baseUrl(env: KalshiEnv): string {
  const e = getEnv();
  return env === 'demo' ? e.KALSHI_DEMO_BASE_URL : e.KALSHI_PROD_DATA_BASE_URL;
}

function signingPath(env: KalshiEnv, path: string): string {
  // path arg is relative like /markets — signing needs /trade-api/v2/...
  const base = new URL(baseUrl(env));
  const full = path.startsWith('/') ? path : `/${path}`;
  return `${base.pathname.replace(/\/$/, '')}${full}`;
}

export async function kalshiRequest<T>(
  path: string,
  opts: RequestOpts = {},
): Promise<T> {
  const {
    method = 'GET',
    body,
    auth = false,
    env = 'production',
    isWrite = method !== 'GET',
    maxAttempts = 5,
  } = opts;

  const url = `${baseUrl(env).replace(/\/$/, '')}${path.startsWith('/') ? path : `/${path}`}`;
  let lastError: unknown;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await acquirePermit(isWrite);

    const headers: Record<string, string> = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    };

    if (auth) {
      const apiKey = requireEnv('KALSHI_API_KEY_ID');
      const pem = getKalshiPrivateKey();
      const signPath = signingPath(env, path);
      Object.assign(headers, authHeaders(apiKey, pem, method, signPath));
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      lastError = err;
      // Network failure after a write is ambiguous
      if (isWrite) {
        throw new KalshiHttpError(
          `Network error on write: ${String(err)}`,
          0,
          '',
          true,
        );
      }
      await sleep(backoffDelayMs(attempt));
      continue;
    }

    if (response.status === 429) {
      incrementHttp429();
      await sleep(backoffDelayMs(attempt));
      continue;
    }

    const text = await response.text();

    if (response.status >= 500) {
      lastError = new KalshiHttpError(
        `Kalshi ${response.status}`,
        response.status,
        text,
        isWrite,
      );
      if (isWrite) throw lastError;
      await sleep(backoffDelayMs(attempt));
      continue;
    }

    if (!response.ok) {
      throw new KalshiHttpError(
        `Kalshi ${response.status}: ${text.slice(0, 300)}`,
        response.status,
        text,
        false,
      );
    }

    if (!text) return {} as T;
    return JSON.parse(text) as T;
  }

  throw lastError instanceof Error
    ? lastError
    : new Error(`Kalshi request failed after retries: ${url}`);
}

/** Verify demo auth before trading operations. */
export async function verifyDemoAuth(): Promise<{ balanceCents: number }> {
  const data = await kalshiRequest<{
    balance?: number;
    balance_dollars?: string;
  }>('/portfolio/balance', { auth: true, env: 'demo' });
  return { balanceCents: data.balance ?? 0 };
}

export async function logAccountLimits(): Promise<void> {
  try {
    const data = await kalshiRequest<{
      read?: { refill_rate?: number; bucket_capacity?: number };
      write?: { refill_rate?: number; bucket_capacity?: number };
      read_limit?: number;
      write_limit?: number;
      usage_limits?: { read_limit?: number; write_limit?: number };
    }>('/account/limits', { auth: true, env: 'demo' });

    // Token-bucket API: refill_rate is tokens/sec; most endpoints cost 10 tokens.
    const read =
      data.read?.refill_rate != null
        ? Math.max(1, Math.floor(data.read.refill_rate / 10))
        : (data.read_limit ?? data.usage_limits?.read_limit ?? 20);
    const write =
      data.write?.refill_rate != null
        ? Math.max(1, Math.floor(data.write.refill_rate / 10))
        : (data.write_limit ?? data.usage_limits?.write_limit ?? 10);

    setRateLimits(read, write);
    console.info(
      `[kalshi] account limits tier=${(data as { usage_tier?: string }).usage_tier ?? '?'} read≈${read}/s write≈${write}/s`,
    );
  } catch (err) {
    if (err instanceof KalshiHttpError && err.status === 401) {
      console.warn(
        '[kalshi] auth failed on /account/limits — verify demo API key + rsa.txt are from demo.kalshi.co and KALSHI_DEMO_BASE_URL is https://external-api.demo.kalshi.co/trade-api/v2',
      );
    }
    console.warn('[kalshi] could not fetch account limits', err);
  }
}

export type ListMarketsOpts = {
  cursor?: string;
  limit?: number;
  status?: string;
  /** Exclude multivariate/combo parlays (KXMVESPORTS… etc.) */
  mveFilter?: 'only' | 'exclude';
  seriesTicker?: string;
  eventTicker?: string;
  /** Unix seconds — pair with maxCloseTs for DTE window */
  minCloseTs?: number;
  maxCloseTs?: number;
};

export async function listOpenMarkets(
  env: KalshiEnv = 'production',
  opts: ListMarketsOpts = {},
): Promise<{ markets: NormalizedMarket[]; cursor: string | null }> {
  const params = new URLSearchParams();
  params.set('limit', String(opts.limit ?? 1000));
  params.set('status', opts.status ?? 'open');
  if (opts.cursor) params.set('cursor', opts.cursor);
  if (opts.mveFilter) params.set('mve_filter', opts.mveFilter);
  if (opts.seriesTicker) params.set('series_ticker', opts.seriesTicker);
  if (opts.eventTicker) params.set('event_ticker', opts.eventTicker);
  if (opts.minCloseTs != null) params.set('min_close_ts', String(opts.minCloseTs));
  if (opts.maxCloseTs != null) params.set('max_close_ts', String(opts.maxCloseTs));

  const data = await kalshiRequest<{
    markets?: unknown[];
    cursor?: string | null;
  }>(`/markets?${params}`, { env, auth: false });

  const markets = (data.markets ?? [])
    .map((m) => KalshiMarketSchema.parse(m))
    .map(normalizeMarket);

  if (markets[0]?.raw) {
    assertDollarPriceFields(markets[0].raw as unknown as Record<string, unknown>);
  }

  return { markets, cursor: data.cursor ?? null };
}

export type FetchMarketsResult = {
  markets: NormalizedMarket[];
  pages: number;
  exhausted: boolean;
  total: number;
};

export type MarketPageStats = {
  pages: number;
  total: number;
  exhausted: boolean;
};

/**
 * Stream every open-market page until the cursor is exhausted.
 * Cursor chains are sequential (API constraint); pass listOpts to pre-filter.
 */
export async function forEachOpenMarketPage(
  env: KalshiEnv,
  onPage: (markets: NormalizedMarket[], meta: { page: number; total: number }) => void | Promise<void>,
  opts?: { maxPages?: number; listOpts?: Omit<ListMarketsOpts, 'cursor'> },
): Promise<MarketPageStats> {
  let cursor: string | null = null;
  let pages = 0;
  let total = 0;
  const cap = opts?.maxPages ?? Number.POSITIVE_INFINITY;
  const listOpts = opts?.listOpts ?? {};

  while (pages < cap) {
    const page = await listOpenMarkets(env, {
      ...listOpts,
      cursor: cursor ?? undefined,
    });
    pages += 1;
    total += page.markets.length;
    await onPage(page.markets, { page: pages, total });
    cursor = page.cursor;
    if (!cursor || page.markets.length === 0) {
      return { pages, total, exhausted: true };
    }
    if (pages % 50 === 0) {
      console.info(`[kalshi] market pages … ${total} markets (${pages} pages)`);
    }
  }

  console.warn(
    `[kalshi] forEachOpenMarketPage hit optional page cap (${cap}); cursor still present — truncated at ${total}`,
  );
  return { pages, total, exhausted: false };
}

/**
 * Collect markets into an array. Prefer forEachOpenMarketPage for full-universe work.
 * maxPages defaults to 100 (20k) to avoid OOM; pass a higher cap only when needed.
 */
export async function fetchAllOpenMarkets(
  env: KalshiEnv = 'production',
  maxPages = 100,
): Promise<NormalizedMarket[]> {
  const { markets } = await fetchAllOpenMarketsDetailed(env, maxPages);
  return markets;
}

export async function fetchAllOpenMarketsDetailed(
  env: KalshiEnv = 'production',
  maxPages = 100,
): Promise<FetchMarketsResult> {
  const all: NormalizedMarket[] = [];
  const stats = await forEachOpenMarketPage(
    env,
    (markets) => {
      all.push(...markets);
    },
    { maxPages },
  );
  return { markets: all, pages: stats.pages, exhausted: stats.exhausted, total: stats.total };
}

export async function getMarket(
  ticker: string,
  env: KalshiEnv = 'production',
): Promise<NormalizedMarket> {
  const data = await kalshiRequest<{ market: unknown }>(`/markets/${ticker}`, {
    env,
    auth: env === 'demo',
  });
  return normalizeMarket(KalshiMarketSchema.parse(data.market));
}

export async function getOrderbookDepth(
  ticker: string,
  env: KalshiEnv = 'production',
): Promise<{ yesAskDepth: number; noAskDepth: number }> {
  const data = await kalshiRequest<{
    orderbook_fp?: {
      yes_dollars?: [string, string][];
      no_dollars?: [string, string][];
    };
  }>(`/markets/${ticker}/orderbook`, { env });

  // Kalshi returns bids for yes/no.
  // Buying NO crosses yes bids (sell YES); buying YES crosses no bids (sell NO).
  const yesBids = data.orderbook_fp?.yes_dollars ?? [];
  const noBids = data.orderbook_fp?.no_dollars ?? [];
  const noAskDepth = Math.floor(Number(yesBids[0]?.[1] ?? 0)) || 0;
  const yesAskDepth = Math.floor(Number(noBids[0]?.[1] ?? 0)) || 0;
  return { yesAskDepth, noAskDepth };
}

export type PlaceOrderInput = {
  ticker: string;
  side: 'yes' | 'no';
  action?: 'buy' | 'sell';
  count: number;
  limitPriceCents: number;
  clientOrderId: string;
};

export async function placeOrder(input: PlaceOrderInput) {
  // V2 single-book API: bid/ask on YES leg. Buy NO == sell YES at (100 - no_price).
  const bookSide = input.side === 'yes' ? 'bid' : 'ask';
  const priceCents =
    input.side === 'yes'
      ? input.limitPriceCents
      : 100 - input.limitPriceCents;

  const body = {
    ticker: input.ticker,
    client_order_id: input.clientOrderId,
    side: bookSide,
    count: input.count.toFixed(2),
    price: centsToDollars(priceCents),
    time_in_force: 'good_till_canceled',
    self_trade_prevention_type: 'taker_at_cross',
  };

  const data = await kalshiRequest<{ order?: unknown }>(
    '/portfolio/events/orders',
    {
      method: 'POST',
      body,
      auth: true,
      env: 'demo',
      isWrite: true,
    },
  );

  return KalshiOrderSchema.parse(data.order ?? data);
}

export async function getOrder(orderId: string) {
  const data = await kalshiRequest<{ order: unknown }>(
    `/portfolio/orders/${orderId}`,
    { auth: true, env: 'demo' },
  );
  return KalshiOrderSchema.parse(data.order);
}

export async function getOrdersByClientOrderId(clientOrderId: string) {
  const params = new URLSearchParams({ client_order_id: clientOrderId });
  const data = await kalshiRequest<{ orders?: unknown[] }>(
    `/portfolio/orders?${params}`,
    { auth: true, env: 'demo' },
  );
  return (data.orders ?? []).map((o) => KalshiOrderSchema.parse(o));
}

export async function cancelOrder(orderId: string) {
  return kalshiRequest(`/portfolio/events/orders/${orderId}`, {
    method: 'DELETE',
    auth: true,
    env: 'demo',
    isWrite: true,
  });
}

export async function getFills(opts: { ticker?: string; orderId?: string } = {}) {
  const params = new URLSearchParams();
  if (opts.ticker) params.set('ticker', opts.ticker);
  if (opts.orderId) params.set('order_id', opts.orderId);
  const q = params.toString();
  const data = await kalshiRequest<{ fills?: unknown[] }>(
    `/portfolio/fills${q ? `?${q}` : ''}`,
    { auth: true, env: 'demo' },
  );
  return (data.fills ?? []).map((f) => KalshiFillSchema.parse(f));
}

export async function getSettlements(cursor?: string) {
  const params = new URLSearchParams({ limit: '100' });
  if (cursor) params.set('cursor', cursor);
  return kalshiRequest<{
    settlements?: Array<{
      ticker?: string;
      market_result?: string;
      yes_count_fp?: string;
      no_count_fp?: string;
    }>;
    cursor?: string | null;
  }>(`/portfolio/settlements?${params}`, { auth: true, env: 'demo' });
}

export const LimitsSchema = z.object({
  read_limit: z.number().optional(),
  write_limit: z.number().optional(),
});
