import fs from 'node:fs';
import path from 'node:path';
import { getEnv, normalizePem } from '@/lib/env';

let cachedPem: string | null = null;

/** @internal test helper */
export function clearKalshiPrivateKeyCache(): void {
  cachedPem = null;
}

function defaultKeyPath(): string {
  return path.join(process.cwd(), 'rsa.txt');
}

/** RSA PEM for Kalshi request signing. Env override for Vercel; local default is rsa.txt. */
export function getKalshiPrivateKey(): string {
  if (cachedPem) return cachedPem;

  const envKey = getEnv().KALSHI_PRIVATE_KEY?.trim();
  if (envKey) {
    cachedPem = normalizePem(envKey);
    return cachedPem;
  }

  const keyPath = process.env.KALSHI_PRIVATE_KEY_PATH ?? defaultKeyPath();
  if (!fs.existsSync(keyPath)) {
    throw new Error(
      `Kalshi private key not found. Add ${keyPath} or set KALSHI_PRIVATE_KEY (Vercel).`,
    );
  }

  cachedPem = fs.readFileSync(keyPath, 'utf8').trim();
  return cachedPem;
}

export function hasKalshiCredentials(): boolean {
  if (!getEnv().KALSHI_API_KEY_ID) return false;
  try {
    getKalshiPrivateKey();
    return true;
  } catch {
    return false;
  }
}
