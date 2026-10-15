// APNs with token auth: an ES256 JWT from the app's .p8 key, made at most every 50 minutes
// (Apple refuses one older than 60, and asks for no more than one new token every 20).

import type { SealedPush } from './validate';
import { base64url } from './crypto';

export interface ApnsKey {
  APNS_KEY: string; // the .p8 file's text (PKCS #8 PEM)
  APNS_KEY_ID: string;
  APNS_TEAM_ID: string;
  APNS_TOPIC: string; // the app's bundle id
}

export const TOKEN_TTL_S = 50 * 60;
const PRODUCTION = 'https://api.push.apple.com';
const SANDBOX = 'https://api.sandbox.push.apple.com';

let cached: { keyId: string; token: string; issuedAt: number } | null = null;

/** For tests: forget the cached JWT. */
export function forgetToken(): void {
  cached = null;
}

async function importKey(pem: string): Promise<CryptoKey> {
  const body = pem.replace(/-----(BEGIN|END) PRIVATE KEY-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey('pkcs8', der, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
}

export async function providerToken(key: ApnsKey, nowS: number): Promise<string> {
  if (cached && cached.keyId === key.APNS_KEY_ID && nowS - cached.issuedAt < TOKEN_TTL_S) {
    return cached.token;
  }
  const encode = (value: unknown) => base64url(new TextEncoder().encode(JSON.stringify(value)));
  const unsigned = `${encode({ alg: 'ES256', kid: key.APNS_KEY_ID })}.${encode({ iss: key.APNS_TEAM_ID, iat: nowS })}`;
  // Web Crypto signs ECDSA as r ‖ s (IEEE P1363), which is what a JWS ES256 signature is.
  const signature = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' },
    await importKey(key.APNS_KEY),
    new TextEncoder().encode(unsigned),
  );
  const token = `${unsigned}.${base64url(new Uint8Array(signature))}`;
  cached = { keyId: key.APNS_KEY_ID, token, issuedAt: nowS };
  return token;
}

/** The APNs payload: fixed words on the lock screen; the phone's extension opens `sealed`. */
export function payload(sealed: SealedPush, urgent: boolean): string {
  return JSON.stringify({
    aps: {
      alert: { title: 'Marcel', body: 'You have news' },
      'mutable-content': 1,
      'interruption-level': urgent ? 'time-sensitive' : 'active',
    },
    sealed,
  });
}

export type Sent = 'ok' | 'gone' | 'failed';

export async function send(
  fetcher: typeof fetch,
  key: ApnsKey,
  nowS: number,
  target: { apns_token: string; sandbox: boolean },
  sealed: SealedPush,
  urgent: boolean,
): Promise<Sent> {
  const origin = target.sandbox ? SANDBOX : PRODUCTION;
  let response: Response;
  try {
    response = await fetcher(`${origin}/3/device/${target.apns_token}`, {
      method: 'POST',
      headers: {
        authorization: `bearer ${await providerToken(key, nowS)}`,
        'apns-topic': key.APNS_TOPIC,
        'apns-push-type': 'alert',
        'apns-priority': urgent ? '10' : '5',
        'content-type': 'application/json',
      },
      body: payload(sealed, urgent),
    });
  } catch {
    return 'failed';
  }
  if (response.status === 200) return 'ok';
  return response.status === 410 ? 'gone' : 'failed';
}
