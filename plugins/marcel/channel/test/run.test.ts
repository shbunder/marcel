import { PassThrough } from 'node:stream';
import { describe, expect, it } from 'vitest';
import { run } from '../src/run.js';
import { FakeHub, SECRET, SESSION_ID } from './harness.js';

const tick = () => new Promise((r) => setTimeout(r, 20));

describe('run', () => {
  it('serves MCP on stdin/stdout, connects with the secret from the data dir, and shuts down when stdin closes', async () => {
    const hub = new FakeHub();
    const stdin = new PassThrough();
    const out: string[] = [];
    let closed = false;
    const server = run({
      env: { CLAUDE_CODE_SESSION_ID: SESSION_ID, CLAUDE_PLUGIN_DATA: '/data' },
      stdin,
      write: (l) => out.push(l),
      log: () => undefined,
      createSocket: hub.createSocket,
      readText: () => SECRET,
      onClose: () => (closed = true),
    });
    stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' })}\n`);
    await tick();
    expect(JSON.parse(out[0]!).result.tools).toHaveLength(2);
    expect(hub.hellos).toHaveLength(1);
    stdin.end();
    await tick();
    expect(closed).toBe(true);
    expect(server.hub?.state).toBe('stopped');
  });

  it('still serves MCP when the secret is missing', async () => {
    const hub = new FakeHub();
    const stdin = new PassThrough();
    const out: string[] = [];
    const logs: string[] = [];
    run({
      env: { CLAUDE_CODE_SESSION_ID: SESSION_ID, CLAUDE_PLUGIN_DATA: '/data' },
      stdin,
      write: (l) => out.push(l),
      log: (l) => logs.push(l),
      createSocket: hub.createSocket,
      readText: () => {
        throw new Error('ENOENT');
      },
      onClose: () => undefined,
    });
    stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' })}\n`);
    await tick();
    expect(out).toHaveLength(1);
    expect(hub.attempts).toBe(0);
    expect(logs[0]).toContain('hub secret is missing');
    stdin.end();
  });
});
