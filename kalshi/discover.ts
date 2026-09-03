import {
  forEachOpenMarketPage,
  listOpenMarkets,
  type KalshiEnv,
  type ListMarketsOpts,
  type MarketPageStats,
} from '@/lib/kalshi/client';
import type { NormalizedMarket } from '@/lib/kalshi/schemas';
import { loadSeriesAllowlist } from '@/lib/kalshi/series-index';
import { MIN_DTE, MAX_DTE } from '@/lib/screen/filters';

export type DiscoverStats = MarketPageStats & {
  mode: 'filtered_stream' | 'series_parallel' | 'hybrid';
  seriesCount?: number;
};

/** Close-time window matching screen DTE gates (unix seconds). */
export function screenCloseWindow(now = new Date()): {
  minCloseTs: number;
  maxCloseTs: number;
} {
  const sec = Math.floor(now.getTime() / 1000);
  return {
    minCloseTs: sec + MIN_DTE * 86400,
    maxCloseTs: sec + MAX_DTE * 86400,
  };
}

/** Server-side filters: drop MVE parlays + DTE close window + fat pages. */
export function screenListFilters(now = new Date()): ListMarketsOpts {
  const { minCloseTs, maxCloseTs } = screenCloseWindow(now);
  return {
    status: 'open',
    limit: 1000,
    mveFilter: 'exclude',
    minCloseTs,
    maxCloseTs,
  };
}

async function mapPool<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  async function worker() {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await fn(items[i]!, i);
    }
  }
  const n = Math.max(1, Math.min(concurrency, items.length || 1));
  await Promise.all(Array.from({ length: n }, () => worker()));
  return results;
}

/** Fetch series tickers for allowlisted categories (single response per category). */
export async function listAllowlistedSeries(
  env: KalshiEnv = 'production',
): Promise<Array<{ ticker: string; category: string }>> {
  const allowlist = await loadSeriesAllowlist(env);
  return [...allowlist.byTicker.entries()].map(([ticker, category]) => ({
    ticker,
    category,
  }));
}

async function fetchAllForSeries(
  env: KalshiEnv,
  seriesTicker: string,
  filters: ListMarketsOpts,
): Promise<NormalizedMarket[]> {
  const all: NormalizedMarket[] = [];
  let cursor: string | null = null;
  for (;;) {
    const page = await listOpenMarkets(env, {
      ...filters,
      seriesTicker,
      cursor: cursor ?? undefined,
    });
    all.push(...page.markets);
    cursor = page.cursor;
    if (!cursor || page.markets.length === 0) break;
  }
  return all;
}

/**
 * Parallel discovery via allowlisted /series → per-series /markets.
 * Low concurrency to stay under public rate limits.
 */
export async function forEachAllowlistedSeriesMarkets(
  env: KalshiEnv,
  onBatch: (markets: NormalizedMarket[]) => void | Promise<void>,
  opts?: { concurrency?: number; now?: Date },
): Promise<DiscoverStats> {
  const { minCloseTs, maxCloseTs } = screenCloseWindow(opts?.now);
  const seriesFilters: ListMarketsOpts = {
    status: 'open',
    limit: 1000,
    minCloseTs,
    maxCloseTs,
  };

  const series = await listAllowlistedSeries(env);
  let total = 0;
  let pages = 0;

  await mapPool(series, opts?.concurrency ?? 4, async (s) => {
    try {
      const markets = await fetchAllForSeries(env, s.ticker, seriesFilters);
      pages += 1;
      if (markets.length === 0) return;
      const tagged = markets.map((m) =>
        m.category ? m : { ...m, category: s.category },
      );
      total += tagged.length;
      await onBatch(tagged);
    } catch (err) {
      console.warn(`[discover] series ${s.ticker} failed`, err);
    }
  });

  return {
    mode: 'series_parallel',
    pages,
    total,
    exhausted: true,
    seriesCount: series.length,
  };
}

/**
 * Stream open markets with MVE exclusion + DTE close window.
 * Cursor pages are sequential; limit=1000 cuts round-trips ~5×.
 */
export async function forEachScreenableMarkets(
  env: KalshiEnv,
  onPage: (
    markets: NormalizedMarket[],
    meta: { page: number; total: number },
  ) => void | Promise<void>,
  opts?: { maxPages?: number; now?: Date },
): Promise<DiscoverStats> {
  const filters = screenListFilters(opts?.now);
  const stats = await forEachOpenMarketPage(env, onPage, {
    maxPages: opts?.maxPages,
    listOpts: filters,
  });
  return { ...stats, mode: 'filtered_stream' };
}

/**
 * Prefer filtered stream (fast, complete for non-MVE open book in DTE window).
 * Optionally merge allowlisted series in parallel for category-tagged coverage.
 */
export async function discoverScreenUniverse(
  env: KalshiEnv,
  onMarket: (market: NormalizedMarket) => void,
  opts?: {
    concurrency?: number;
    now?: Date;
    /** Also fan out allowlisted /series in parallel (default true). */
    includeSeries?: boolean;
  },
): Promise<DiscoverStats> {
  const seen = new Set<string>();
  let total = 0;

  const emit = (m: NormalizedMarket) => {
    if (seen.has(m.ticker)) return;
    seen.add(m.ticker);
    total += 1;
    onMarket(m);
  };

  // Primary: one filtered cursor chain (mve_exclude + close window + limit 1000)
  const streamStats = await forEachScreenableMarkets(
    env,
    (markets) => {
      for (const m of markets) emit(m);
    },
    { now: opts?.now },
  );

  let seriesCount: number | undefined;
  let seriesPages = 0;

  if (opts?.includeSeries !== false) {
    try {
      const seriesStats = await forEachAllowlistedSeriesMarkets(
        env,
        (markets) => {
          for (const m of markets) emit(m);
        },
        { concurrency: opts?.concurrency ?? 4, now: opts?.now },
      );
      seriesCount = seriesStats.seriesCount;
      seriesPages = seriesStats.pages;
    } catch (err) {
      console.warn('[discover] series parallel pass failed', err);
    }
  }

  return {
    mode: seriesCount != null ? 'hybrid' : 'filtered_stream',
    pages: streamStats.pages + seriesPages,
    total,
    exhausted: streamStats.exhausted,
    seriesCount,
  };
}

/** Run async work over items with a concurrency cap. */
export async function parallelMap<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  return mapPool(items, concurrency, (item) => fn(item));
}

/**
 * Discover screened candidates until `targetPassers` found (or series exhausted).
 * Faster than full `discoverScreenUniverse` for sample / dry-run scripts.
 */
export async function discoverScreenCandidatesUntil(
  env: KalshiEnv,
  targetPassers: number,
  screen: (market: NormalizedMarket) => unknown,
  opts?: { now?: Date; buffer?: number },
): Promise<{ marketsSeen: number; seriesScanned: number }> {
  const buffer = opts?.buffer ?? Math.max(targetPassers * 3, 10);
  const seen = new Set<string>();
  let marketsSeen = 0;
  let passers = 0;

  const emit = (m: NormalizedMarket) => {
    if (seen.has(m.ticker)) return;
    seen.add(m.ticker);
    marketsSeen += 1;
    if (screen(m)) passers += 1;
  };

  await forEachScreenableMarkets(
    env,
    (markets) => {
      for (const m of markets) {
        emit(m);
        if (passers >= buffer) return;
      }
    },
    { now: opts?.now },
  );

  if (passers >= targetPassers) {
    return { marketsSeen, seriesScanned: 0 };
  }

  const { minCloseTs, maxCloseTs } = screenCloseWindow(opts?.now);
  const seriesFilters: ListMarketsOpts = {
    status: 'open',
    limit: 1000,
    minCloseTs,
    maxCloseTs,
  };
  const series = await listAllowlistedSeries(env);
  let seriesScanned = 0;

  for (const s of series) {
    if (passers >= buffer) break;
    seriesScanned += 1;
    try {
      const markets = await fetchAllForSeries(env, s.ticker, seriesFilters);
      for (const m of markets) {
        emit(m.category ? m : { ...m, category: s.category });
        if (passers >= buffer) break;
      }
    } catch (err) {
      console.warn(`[discover] series ${s.ticker} failed`, err);
    }
  }

  return { marketsSeen, seriesScanned };
}
