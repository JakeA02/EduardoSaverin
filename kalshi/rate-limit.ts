/** Process-wide token bucket + 429 backoff helpers. */

type LimiterState = {
  tokens: number;
  lastRefill: number;
  readPerSec: number;
  writePerSec: number;
  http429Count: number;
};

const globalForLimiter = globalThis as unknown as { __kalshiLimiter?: LimiterState };

function state(): LimiterState {
  if (!globalForLimiter.__kalshiLimiter) {
    globalForLimiter.__kalshiLimiter = {
      tokens: 20,
      lastRefill: Date.now(),
      readPerSec: 20,
      writePerSec: 10,
      http429Count: 0,
    };
  }
  return globalForLimiter.__kalshiLimiter;
}

export function setRateLimits(readPerSec: number, writePerSec: number): void {
  const s = state();
  s.readPerSec = readPerSec;
  s.writePerSec = writePerSec;
  s.tokens = Math.min(s.tokens, readPerSec);
}

export function getHttp429Count(): number {
  return state().http429Count;
}

export function incrementHttp429(): void {
  state().http429Count += 1;
}

function refill(isWrite: boolean): void {
  const s = state();
  const now = Date.now();
  const elapsed = (now - s.lastRefill) / 1000;
  const rate = isWrite ? s.writePerSec : s.readPerSec;
  s.tokens = Math.min(rate, s.tokens + elapsed * rate);
  s.lastRefill = now;
}

export async function acquirePermit(isWrite: boolean): Promise<void> {
  for (;;) {
    refill(isWrite);
    const s = state();
    if (s.tokens >= 1) {
      s.tokens -= 1;
      return;
    }
    await sleep(50);
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Bounded exponential backoff with full jitter. */
export function backoffDelayMs(attempt: number, baseMs = 500, maxAttempts = 5): number {
  const exp = Math.min(attempt, maxAttempts - 1);
  const max = baseMs * 2 ** exp;
  return Math.floor(Math.random() * max);
}
