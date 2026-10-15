// The relay's routes (contracts/push-relay.yaml): phones register for a handle and a send secret,
// hubs push sealed notifications to a handle, and the relay hands the sealed bytes to APNs untouched.
//
// What it never does: log or store a request body, open a sealed push (it has no key for it), or
// take a push for a handle without that handle's secret. The only lines it prints are fixed words
// with a status code; test/never-logs.test.ts holds it to that.

import { send, type ApnsKey } from './apns';
import { newHandle, newSendSecret, sha256Hex } from './crypto';
import { UNAUTHORIZED, error, json } from './http';
import { addHandle, countPush, deleteHandle, dropHandle } from './store';
import { HANDLE, push, registration } from './validate';

export interface Env extends ApnsKey {
  DB: D1Database;
}

/** What the relay reaches outside itself; tests pass a fake APNs and a fixed clock. */
export interface Deps {
  fetch: typeof fetch;
  now: () => Date;
}

export const MAX_BODY = 3 * 1024;
const BEARER = /^Bearer ([A-Za-z0-9_-]{43})$/i;

type Body = { kind: 'ok'; value: unknown } | { kind: 'too_large' } | { kind: 'bad' };

/** Reads at most `MAX_BODY` bytes: a longer body is refused without reading the rest. */
async function readBody(request: Request): Promise<Body> {
  const declared = Number(request.headers.get('content-length') ?? '0');
  if (declared > MAX_BODY) return { kind: 'too_large' };
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (request.body) {
    const reader = request.body.getReader();
    for (let part = await reader.read(); !part.done; part = await reader.read()) {
      size += part.value.byteLength;
      if (size > MAX_BODY) {
        await reader.cancel();
        return { kind: 'too_large' };
      }
      chunks.push(part.value);
    }
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  try {
    return { kind: 'ok', value: JSON.parse(new TextDecoder().decode(bytes)) };
  } catch {
    return { kind: 'bad' };
  }
}

function bearer(request: Request): string | null {
  return BEARER.exec(request.headers.get('authorization') ?? '')?.[1] ?? null;
}

async function register(request: Request, env: Env): Promise<Response> {
  const body = await readBody(request);
  if (body.kind === 'too_large') return error('too_large', 'The request is over 3 KB.');
  const reg = body.kind === 'ok' ? registration(body.value) : null;
  if (!reg) return error('bad_request', 'Send {apns_token, sandbox} as the contract says.');
  const handle = newHandle();
  const sendSecret = newSendSecret();
  await addHandle(env.DB, handle, reg.apns_token, await sha256Hex(sendSecret), reg.sandbox);
  return json(201, { handle, send_secret: sendSecret });
}

async function sendPush(request: Request, env: Env, deps: Deps): Promise<Response> {
  const body = await readBody(request);
  if (body.kind === 'too_large') return error('too_large', 'The request is over 3 KB. Nothing was sent.');
  const secret = bearer(request);
  if (!secret) return UNAUTHORIZED();
  const req = body.kind === 'ok' ? push(body.value) : null;
  if (!req) return error('bad_request', 'Send {handle, sealed, urgent} as the contract says.');
  const now = deps.now();
  const day = now.toISOString().slice(0, 10);
  const counted = await countPush(env.DB, req.handle, await sha256Hex(secret), day);
  if (counted.kind === 'unauthorized') return UNAUTHORIZED();
  if (counted.kind === 'over_limit') {
    return error('too_many_pushes', 'Over 500 pushes today for this phone. It resets at midnight UTC.');
  }
  const sent = await send(deps.fetch, env, Math.floor(now.getTime() / 1000), counted.target, req.sealed, req.urgent);
  if (sent === 'gone') {
    await dropHandle(env.DB, req.handle);
    return error('handle_gone', 'Apple says this phone’s token is gone. The phone registers again.');
  }
  if (sent === 'failed') {
    console.warn('APNs did not take a push.'); // fixed words only: never the body or the token
    return error('apns_unavailable', 'Apple did not take the push. Try again later.');
  }
  return json(202, { ok: true });
}

async function forget(request: Request, env: Env, handle: string): Promise<Response> {
  const secret = bearer(request);
  if (!secret || !HANDLE.test(handle)) return UNAUTHORIZED();
  const gone = await deleteHandle(env.DB, handle, await sha256Hex(secret));
  return gone ? new Response(null, { status: 204 }) : UNAUTHORIZED();
}

export async function handle(request: Request, env: Env, deps: Deps): Promise<Response> {
  const { pathname } = new URL(request.url);
  if (pathname === '/v1/register' && request.method === 'POST') return register(request, env);
  if (pathname === '/v1/push' && request.method === 'POST') return sendPush(request, env, deps);
  const handleMatch = /^\/v1\/handles\/([^/]+)$/.exec(pathname);
  if (handleMatch?.[1] && request.method === 'DELETE') return forget(request, env, handleMatch[1]);
  return error('not_found', 'No such route.');
}
