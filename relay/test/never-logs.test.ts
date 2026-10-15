// The relay never logs a request body (S-22.14, contracts/push-relay.yaml): no `console.log` in its
// source, every other console line is fixed words, and a full run prints nothing of what it carried.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SEALED, TOKEN, registered, relay } from './helpers';

const sources = import.meta.glob('../src/**/*.ts', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>;

describe('never logs a request body', () => {
  it('has no console.log in relay/src, and only fixed words in its other console lines', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(3);
    for (const [file, text] of Object.entries(sources)) {
      expect(text, file).not.toMatch(/console\.log\s*\(/);
      // No way around the line check below: no console[...] or a console kept under another name.
      expect(text, file).not.toMatch(/console\s*\[|=\s*console\b|globalThis\.console|\bconsole\s*[,)}]/);
      for (const line of text.match(/console\.\w+\s*\([^)]*\)/g) ?? []) {
        expect(line, `${file}: ${line}`).toMatch(/^console\.\w+\s*\('[^'`$]*'\)$/);
      }
    }
  });

  describe('a full run', () => {
    const printed: string[] = [];
    beforeEach(() => {
      for (const level of ['log', 'info', 'warn', 'error', 'debug'] as const) {
        vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
          printed.push(args.map(String).join(' '));
        });
      }
    });
    afterEach(() => {
      vi.restoreAllMocks();
      printed.length = 0;
    });

    it('prints nothing of the sealed push, the token or the secret', async () => {
      const r = await relay();
      const { handle, send_secret } = await registered(r);
      await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, send_secret);
      await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, 'x'.repeat(43)); // 401
      await r.call('POST', '/v1/push', { handle, sealed: { ...SEALED, v: 9 }, urgent: true }, send_secret); // 400
      await r.call('POST', '/v1/push', { handle, sealed: { ...SEALED, box: 'A'.repeat(3100) }, urgent: true }, send_secret); // 413
      r.apns.status = 410;
      const other = await registered(r);
      await r.call('POST', '/v1/push', { handle: other.handle, sealed: SEALED, urgent: true }, other.send_secret);
      r.apns.status = 500;
      await r.call('POST', '/v1/push', { handle, sealed: SEALED, urgent: true }, send_secret);
      const all = printed.join('\n');
      for (const secret of [SEALED.box, SEALED.eph, send_secret, handle, other.send_secret, TOKEN]) {
        expect(all).not.toContain(secret);
      }
      expect(all).toContain('APNs did not take a push.');
    });
  });
});
