// The request bodies of contracts/push-relay.yaml, checked by hand: `additionalProperties: false`
// and the patterns, so nothing the contract does not name is passed on.

export interface Registration {
  apns_token: string;
  sandbox: boolean;
}

export interface SealedPush {
  v: 1;
  eph: string;
  nonce: string;
  box: string;
}

export interface Push {
  handle: string;
  sealed: SealedPush;
  urgent: boolean;
}

export const HANDLE = /^[A-Za-z0-9_-]{22}$/;
const APNS_TOKEN = /^[0-9a-fA-F]{64,200}$/;
const EPH = /^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/;
const NONCE = /^[A-Za-z0-9_-]{16}$/;
const BOX = /^[A-Za-z0-9_-]{22,}$/;
const BOX_MAX = 2700;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function onlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).every((k) => keys.includes(k)) && keys.every((k) => k in value);
}

export function registration(value: unknown): Registration | null {
  if (!isObject(value) || !onlyKeys(value, ['apns_token', 'sandbox'])) return null;
  const { apns_token, sandbox } = value;
  if (typeof apns_token !== 'string' || !APNS_TOKEN.test(apns_token)) return null;
  if (typeof sandbox !== 'boolean') return null;
  return { apns_token, sandbox };
}

export function sealedPush(value: unknown): SealedPush | null {
  if (!isObject(value) || !onlyKeys(value, ['v', 'eph', 'nonce', 'box'])) return null;
  const { v, eph, nonce, box } = value;
  if (v !== 1) return null;
  if (typeof eph !== 'string' || !EPH.test(eph)) return null;
  if (typeof nonce !== 'string' || !NONCE.test(nonce)) return null;
  if (typeof box !== 'string' || box.length > BOX_MAX || !BOX.test(box)) return null;
  return { v, eph, nonce, box };
}

export function push(value: unknown): Push | null {
  if (!isObject(value) || !onlyKeys(value, ['handle', 'sealed', 'urgent'])) return null;
  const { handle, urgent } = value;
  if (typeof handle !== 'string' || !HANDLE.test(handle)) return null;
  if (typeof urgent !== 'boolean') return null;
  const sealed = sealedPush(value.sealed);
  return sealed && { handle, sealed, urgent };
}
