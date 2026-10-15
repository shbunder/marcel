import { describe, expect, it } from 'vitest';
import * as entry from '../src/index';
import worker from '../src/index';
import { TOKEN_TTL_S } from '../src/apns';
import { DAILY_LIMIT } from '../src/store';
import { SEALED, TOKEN, registered, relay } from './helpers';

const b64urlBytes = (text: string) =>
  Uint8Array.from(atob(text.replaceAll('-', '+').replaceAll('_', '/')), (c) => c.charCodeAt(0));

describe('register', () => {
  it('gives a handle and a send secret, and stores only the secret’s hash', async () => {
    const r = await relay();
    const res = await r.call('POST', '/v1/register', { apns_token: TOKEN, sandbox: false });
    expect(res.status).toBe(201);
    const body = await res.json<{ handle: string; send_secret: string }>();
    expect(Object.keys(body).sort()).toEqual(['handle', 'send_secret']);
    expect(body.handle).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(body.send_secret).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const row = await r.env.DB.prepare('SELECT * FROM handles WHERE handle = ?1').bind(body.handle).first();
    expect(JSON.stringify(row)).not.toContain(body.send_secret);
    expect(row).toMatchObject({ apns_token: TOKEN, sandbox: 0, count: 0 });
  });

  it.each([
    [{ apns_token: TOKEN }],
    [{ apns_token: 'xyz', sandbox: false }],
    [{ apns_token: TOKEN, sandbox: 'no' }],
    [{ apns_token: TOKEN, sandbox: false, name: 'extra' }],
    [[TOKEN]],
  ])('refuses a bad registration %j', async (body) => {
    const r = await relay();
    const res = await r.call('POST', '/v1/register', body);
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ code: 'bad_request' });
  });

  it('refuses a body that is not JSON', async () => {
    const r = await relay();
    expect((await r.call('POST', '/v1/register', undefined, undefined, '{')).status).toBe(400);
  });
});

describe('push', () => {
  it('hands the sealed bytes to APNs once, untouched, with a signed token', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const res = await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, send_secret);
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ ok: true });
    expect(r.apns.calls).toHaveLength(1);
    const [call] = r.apns.calls;
    expect(call?.url).toBe(`https://api.push.apple.com/3/device/${TOKEN}`);
    expect(call?.headers).toMatchObject({
      'apns-topic': 'app.marcel.ios',
      'apns-push-type': 'alert',
      'apns-priority': '10',
    });
    const sent = JSON.parse(call?.body ?? '');
    expect(sent).toEqual({
      aps: {
        alert: { title: 'Marcel', body: 'You have news' },
        'mutable-content': 1,
        'interruption-level': 'time-sensitive',
      },
      sealed: SEALED,
    });
    expect(call?.body).toContain(JSON.stringify(SEALED)); // the same bytes, in the same order

    const jwt = (call?.headers.authorization ?? '').replace(/^bearer /, '');
    const [header, claims, signature] = jwt.split('.');
    expect(JSON.parse(atob(header ?? ''))).toEqual({ alg: 'ES256', kid: 'KEY1234567' });
    expect(JSON.parse(atob(claims ?? ''))).toEqual({
      iss: 'TEAM123456',
      iat: Math.floor(r.clock.at.getTime() / 1000),
    });
    const valid = await crypto.subtle.verify(
      { name: 'ECDSA', hash: 'SHA-256' },
      r.publicKey,
      b64urlBytes(signature ?? ''),
      new TextEncoder().encode(`${header}.${claims}`),
    );
    expect(valid).toBe(true);
  });

  it('sends a quiet push at priority 5, and a development build to the APNs sandbox', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r, true);
    await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: false }, send_secret);
    const [call] = r.apns.calls;
    expect(call?.url).toBe(`https://api.sandbox.push.apple.com/3/device/${TOKEN}`);
    expect(call?.headers['apns-priority']).toBe('5');
    expect(JSON.parse(call?.body ?? '').aps['interruption-level']).toBe('active');
  });

  it('keeps its APNs token for 50 minutes, then makes a new one', async () => {
    expect(TOKEN_TTL_S).toBe(50 * 60); // Apple refuses a token older than 60 minutes
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const body = { handle, sealed: SEALED, urgent: false };
    await r.call('POST', '/v1/push', body, send_secret);
    r.clock.advance(TOKEN_TTL_S - 1);
    await r.call('POST', '/v1/push', body, send_secret);
    r.clock.advance(1);
    await r.call('POST', '/v1/push', body, send_secret);
    const tokens = r.apns.calls.map((c) => c.headers.authorization);
    expect(tokens[0]).toBe(tokens[1]);
    expect(tokens[2]).not.toBe(tokens[1]);
  });

  it('refuses a wrong secret with 401 and never calls APNs', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const wrong = `${send_secret.slice(0, 42)}${send_secret.endsWith('A') ? 'B' : 'A'}`;
    const body = { handle, sealed: SEALED, urgent: true };
    for (const secret of [wrong, undefined]) {
      const res = await r.call('POST', '/v1/push', body, secret);
      expect(res.status).toBe(401);
      expect(await res.json()).toMatchObject({ code: 'unauthorized' });
    }
    expect(r.apns.calls).toHaveLength(0);
  });

  it('answers a handle that does not exist exactly as a wrong secret', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const wrong = await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, 'x'.repeat(43));
    const unknown = await r.call(
      'POST',
      '/v1/push',
      { handle: 'A'.repeat(22), sealed: SEALED, urgent: true },
      send_secret,
    );
    expect(unknown.status).toBe(401);
    expect(await unknown.json()).toEqual(await wrong.json());
  });

  it.each([
    [{ sealed: SEALED, urgent: true }],
    [{ handle: 'short', sealed: SEALED, urgent: true }],
    [{ handle: 'A'.repeat(22), sealed: { ...SEALED, v: 2 }, urgent: true }],
    [{ handle: 'A'.repeat(22), sealed: { ...SEALED, text: 'peek' }, urgent: true }],
    [{ handle: 'A'.repeat(22), sealed: SEALED, urgent: 'yes' }],
    [{ handle: 'A'.repeat(22), sealed: SEALED, urgent: true, text: 'hi' }],
  ])('refuses a bad push %j with 400', async (body) => {
    const r = await relay();
    const res = await r.call('POST', '/v1/push', body, 'x'.repeat(43));
    expect(res.status).toBe(400);
    expect(r.apns.calls).toHaveLength(0);
  });

  it('refuses a body over 3 KB with 413, by its length or its header', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const big = JSON.stringify({ handle, sealed: { ...SEALED, box: 'A'.repeat(3100) }, urgent: true });
    const res = await r.call('POST', '/v1/push', undefined, send_secret, big);
    expect(res.status).toBe(413);
    expect(await res.json()).toMatchObject({ code: 'too_large' });
    const declared = await worker.fetch(
      new Request('https://relay.test/v1/register', {
        method: 'POST',
        headers: { 'content-length': '5000' },
        body: '{}',
      }),
      r.env,
    );
    expect(declared.status).toBe(413);
    const streamed = new ReadableStream({
      start(controller) {
        for (let i = 0; i < 4; i++) controller.enqueue(new TextEncoder().encode('A'.repeat(1024)));
        controller.close();
      },
    });
    const chunked = await worker.fetch(
      new Request('https://relay.test/v1/register', { method: 'POST', body: streamed }),
      r.env,
    );
    expect(chunked.status).toBe(413);
    expect(r.apns.calls).toHaveLength(0);
  });

  it('takes 500 pushes a day per handle (the contract); the 501st is 429 until midnight UTC', async () => {
    expect(DAILY_LIMIT).toBe(500);
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    const body = { handle, sealed: SEALED, urgent: false };
    for (let i = 0; i < 500; i++) {
      expect((await r.call('POST', '/v1/push', body, send_secret)).status).toBe(202);
    }
    const over = await r.call('POST', '/v1/push', body, send_secret);
    expect(over.status).toBe(429);
    expect(await over.json()).toMatchObject({ code: 'too_many_pushes' });
    expect(r.apns.calls).toHaveLength(500);
    r.clock.at = new Date('2026-10-16T00:00:00Z');
    expect((await r.call('POST', '/v1/push', body, send_secret)).status).toBe(202);
  });

  it('never lets two pushes at once both take the last places of the day', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    await r.env.DB.prepare('UPDATE handles SET day = ?1, count = ?2')
      .bind('2026-10-15', DAILY_LIMIT - 3)
      .run();
    const body = { handle, sealed: SEALED, urgent: false };
    const answers = await Promise.all(
      Array.from({ length: 10 }, () => r.call('POST', '/v1/push', body, send_secret)),
    );
    expect(answers.filter((a) => a.status === 202)).toHaveLength(3);
    expect(answers.filter((a) => a.status === 429)).toHaveLength(7);
  });

  it('forgets the handle when APNs says the token is gone (410)', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    r.apns.status = 410;
    const body = { handle, sealed: SEALED, urgent: true };
    const res = await r.call('POST', '/v1/push', body, send_secret);
    expect(res.status).toBe(410);
    expect(await res.json()).toMatchObject({ code: 'handle_gone' });
    expect(await r.env.DB.prepare('SELECT 1 FROM handles WHERE handle = ?1').bind(handle).first()).toBeNull();
    r.apns.status = 200;
    expect((await r.call('POST', '/v1/push', body, send_secret)).status).toBe(401);
  });

  it.each([[500], [403], ['network']])('answers 502 when APNs does not take it (%s)', async (failure) => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    if (failure === 'network') r.apns.throws = true;
    else r.apns.status = failure as number;
    const res = await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, send_secret);
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({ code: 'apns_unavailable' });
    expect(await r.env.DB.prepare('SELECT 1 FROM handles WHERE handle = ?1').bind(handle).first()).not.toBeNull();
  });
});

describe('delete a handle', () => {
  it('forgets it with its secret; afterwards a push answers 401', async () => {
    const r = await relay();
    const { handle, send_secret } = await registered(r);
    expect((await r.call('DELETE', `/v1/handles/${handle}`, undefined, 'x'.repeat(43))).status).toBe(401);
    expect((await r.call('DELETE', `/v1/handles/${handle}`)).status).toBe(401);
    expect((await r.call('DELETE', '/v1/handles/short', undefined, send_secret)).status).toBe(401);
    expect((await r.call('DELETE', `/v1/handles/${handle}`, undefined, send_secret)).status).toBe(204);
    const res = await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, send_secret);
    expect(res.status).toBe(401);
    expect(r.apns.calls).toHaveLength(0);
  });
});

describe('routes', () => {
  it.each([
    ['GET', '/v1/push'],
    ['GET', '/'],
    ['PUT', '/v1/handles/AAAAAAAAAAAAAAAAAAAAAA'],
  ])('%s %s is not found', async (method, path) => {
    const r = await relay();
    const res = await r.call(method, path);
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ code: 'not_found' });
  });

  it('the entry point exports only its handler (the Workers runtime refuses anything else)', () => {
    expect(Object.keys(entry)).toEqual(['default']);
  });

  it('the deployed entry point answers through the same routes', async () => {
    const r = await relay();
    const res = await worker.fetch(new Request('https://relay.test/nowhere'), r.env);
    expect(res.status).toBe(404);
  });
});
