// A fake APNs, a fixed clock and a real P-256 key, so the relay runs end to end in the Workers
// runtime (Miniflare, local D1) without reaching Apple or Cloudflare.

import { applyD1Migrations, env } from 'cloudflare:test';
import { forgetToken } from '../src/apns';
import { handle, type Deps, type Env } from '../src/relay';

export const SEALED = {
  v: 1,
  eph: 'v7px1ONb5JHvx-TbJRP_rbBg-ZXscLCXlhlyV4X-jBc',
  nonce: 'LMmDNiZF6G2zWF-M',
  box: 'T5yBwnhfhVQsOsjfAboIzrEdSeZ6Os7E1Bp1zwDDI7d5Trz3TexciQlrCA0O7e1kml0QXm5UzlBr7F2csdBbnwtZuRWn5c67bPMaQS0sL45EZ_dE1IA-yV6Hz02XcKQTUXsG0YqlnnkwLWIeWrKJC6iyCKwffFWqBaZ6FPBKDF8Br3bwB7CNxql0bicKihz6du-IVFlggHKWQBBI4JDSmh5jiACx175KlRKf50w0Dw',
} as const;
export const TOKEN = '9f2c4e1a7b3d5f60819a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70';
export const BASE = 'https://relay.test';

export interface Call {
  url: string;
  headers: Record<string, string>;
  body: string;
}

export class FakeApns {
  calls: Call[] = [];
  status = 200;
  reason = 'Unregistered';
  rawBody: string | null = null;
  throws = false;

  fetch: typeof fetch = async (input, init) => {
    if (this.throws) throw new Error('network down');
    const headers = Object.fromEntries(new Headers(init?.headers).entries());
    this.calls.push({ url: String(input), headers, body: String(init?.body) });
    const body = this.status === 200 ? null : (this.rawBody ?? JSON.stringify({ reason: this.reason }));
    return new Response(body, { status: this.status });
  };
}

export class Clock {
  constructor(public at = new Date('2026-10-15T09:00:00Z')) {}
  now = (): Date => this.at;
  advance(seconds: number): void {
    this.at = new Date(this.at.getTime() + seconds * 1000);
  }
}

function pem(der: ArrayBuffer): string {
  let text = '';
  for (const b of new Uint8Array(der)) text += String.fromCharCode(b);
  const lines = btoa(text).match(/.{1,64}/g) ?? [];
  return `-----BEGIN PRIVATE KEY-----\n${lines.join('\n')}\n-----END PRIVATE KEY-----\n`;
}

export interface Relay {
  env: Env;
  apns: FakeApns;
  clock: Clock;
  publicKey: CryptoKey;
  call: (method: string, path: string, body?: unknown, secret?: string, raw?: string) => Promise<Response>;
}

export async function relay(): Promise<Relay> {
  await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
  forgetToken();
  const pair = (await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
    'sign',
    'verify',
  ])) as CryptoKeyPair;
  const relayEnv: Env = {
    DB: env.DB,
    APNS_KEY: pem((await crypto.subtle.exportKey('pkcs8', pair.privateKey)) as ArrayBuffer),
    APNS_KEY_ID: 'KEY1234567',
    APNS_TEAM_ID: 'TEAM123456',
    APNS_TOPIC: 'app.marcel.ios',
  };
  const apns = new FakeApns();
  const clock = new Clock();
  const deps: Deps = { fetch: apns.fetch, now: clock.now };
  const call = (method: string, path: string, body?: unknown, secret?: string, raw?: string) => {
    const headers: Record<string, string> = { 'content-type': 'application/json' };
    if (secret) headers.authorization = `Bearer ${secret}`;
    const text = raw ?? (body === undefined ? undefined : JSON.stringify(body));
    return handle(new Request(`${BASE}${path}`, { method, headers, body: text }), relayEnv, deps);
  };
  return { env: relayEnv, apns, clock, publicKey: pair.publicKey, call };
}

export async function registered(r: Relay, sandbox = false): Promise<{ handle: string; send_secret: string }> {
  const res = await r.call('POST', '/v1/register', { apns_token: TOKEN, sandbox });
  if (res.status !== 201) throw new Error(`register answered ${res.status}`);
  return res.json();
}
