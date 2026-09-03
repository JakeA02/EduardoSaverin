import crypto from 'node:crypto';

/**
 * Kalshi RSA-PSS request signing.
 * Message = timestamp + METHOD + path (no query string, includes /trade-api/v2/...).
 */
export function signKalshiRequest(
  privateKeyPem: string,
  timestampMs: string,
  method: string,
  pathWithQuery: string,
): string {
  const path = pathWithQuery.split('?')[0] ?? pathWithQuery;
  const message = `${timestampMs}${method.toUpperCase()}${path}`;
  const signature = crypto.sign('sha256', Buffer.from(message), {
    key: privateKeyPem,
    padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
    saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
  });
  return signature.toString('base64');
}

export function verifyKalshiSignature(
  publicKeyPem: string,
  timestampMs: string,
  method: string,
  pathWithQuery: string,
  signatureBase64: string,
): boolean {
  const path = pathWithQuery.split('?')[0] ?? pathWithQuery;
  const message = `${timestampMs}${method.toUpperCase()}${path}`;
  return crypto.verify(
    'sha256',
    Buffer.from(message),
    {
      key: publicKeyPem,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
    },
    Buffer.from(signatureBase64, 'base64'),
  );
}

export function authHeaders(
  apiKeyId: string,
  privateKeyPem: string,
  method: string,
  fullPath: string,
  timestampMs: string = String(Date.now()),
): Record<string, string> {
  return {
    'KALSHI-ACCESS-KEY': apiKeyId,
    'KALSHI-ACCESS-TIMESTAMP': timestampMs,
    'KALSHI-ACCESS-SIGNATURE': signKalshiRequest(
      privateKeyPem,
      timestampMs,
      method,
      fullPath,
    ),
    'Content-Type': 'application/json',
  };
}
