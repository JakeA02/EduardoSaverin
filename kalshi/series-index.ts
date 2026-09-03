import { kalshiRequest, type KalshiEnv } from '@/lib/kalshi/client';
import type { NormalizedMarket } from '@/lib/kalshi/schemas';
import {
  CATEGORY_ALLOWLIST,
  GEOPOLITICS_EXCLUDE_PATTERNS,
  GEOPOLITICS_INCLUDE_PATTERNS,
} from '@/lib/theme-tags';

/**
 * Kalshi markets usually omit `category`. Category lives on /series and /events.
 * `/events?category=` is unreliable (ignored); `/series?category=` works but can
 * return a few cross-category rows — we keep only series whose own category is
 * in CATEGORY_ALLOWLIST AND that look like geopolitics / foreign affairs.
 * Match market tickers to allowlisted series via hyphenated prefix.
 */
export type SeriesAllowlist = {
  /** series ticker → Kalshi category */
  byTicker: Map<string, string>;
};

type SeriesRow = {
  ticker?: string;
  title?: string;
  category?: string;
};

function categoryInAllowlist(category: string): boolean {
  return CATEGORY_ALLOWLIST.some((c) => c.toLowerCase() === category.toLowerCase());
}

function seriesLooksGeopolitical(s: SeriesRow): boolean {
  const hay = `${s.ticker ?? ''} ${s.title ?? ''}`;
  if (GEOPOLITICS_EXCLUDE_PATTERNS.some((re) => re.test(hay))) return false;
  return GEOPOLITICS_INCLUDE_PATTERNS.some((re) => re.test(hay));
}

export async function loadSeriesAllowlist(
  env: KalshiEnv = 'production',
): Promise<SeriesAllowlist> {
  const byTicker = new Map<string, string>();

  for (const category of CATEGORY_ALLOWLIST) {
    try {
      const data = await kalshiRequest<{ series?: SeriesRow[] }>(
        `/series?category=${encodeURIComponent(category)}`,
        { env, auth: false },
      );
      for (const s of data.series ?? []) {
        if (!s.ticker || byTicker.has(s.ticker)) continue;
        const cat = s.category ?? category;
        // `/series?category=` occasionally leaks other categories. Trust series' own category.
        if (!categoryInAllowlist(cat)) continue;
        if (!seriesLooksGeopolitical(s)) continue;
        byTicker.set(s.ticker, cat);
      }
    } catch (err) {
      console.warn(`[series-index] failed for ${category}`, err);
    }
  }

  return { byTicker };
}

/** Longest hyphen-prefix of market ticker that matches an allowlisted series. */
export function resolveSeriesTicker(
  marketTicker: string,
  allowlist: SeriesAllowlist,
): string | null {
  const parts = marketTicker.split('-');
  for (let i = parts.length; i >= 1; i--) {
    const candidate = parts.slice(0, i).join('-');
    if (allowlist.byTicker.has(candidate)) return candidate;
  }
  return null;
}

export function applySeriesCategory(
  market: NormalizedMarket,
  allowlist: SeriesAllowlist,
): NormalizedMarket {
  const series = resolveSeriesTicker(market.ticker, allowlist);
  if (!series) return market;
  const category = allowlist.byTicker.get(series);
  if (!category || market.category === category) return market;
  return { ...market, category };
}

export function seriesAllowlisted(
  market: NormalizedMarket,
  allowlist: SeriesAllowlist,
): boolean {
  return resolveSeriesTicker(market.ticker, allowlist) != null;
}
