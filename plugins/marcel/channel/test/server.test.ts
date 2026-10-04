import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INSTRUCTIONS } from '../src/server.js';
import { CONFIG, Harness, SECRET, SESSION_ID } from './harness.js';

const flush = () => vi.advanceTimersByTimeAsync(0);
const text = (r: { result?: { content?: { text: string }[] } }) => r.result?.content?.[0]?.text ?? '';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

async function connected(h = new Harness()) {
  h.server.start();
  await h.handshake();
  await flush();
  return h;
}

describe('MCP handshake', () => {
  it('declares the channel and permission capabilities, with one set of instructions', async () => {
    const h = new Harness();
    const res = await h.request('initialize', { protocolVersion: '2025-03-26' });
    const r = res.result as { capabilities: { experimental: Record<string, unknown>; tools: unknown }; instructions: string; protocolVersion: string };
    expect(r.capabilities.experimental).toEqual({ 'claude/channel': {}, 'claude/channel/permission': {} });
    expect(r.capabilities.tools).toEqual({});
    expect(r.instructions).toBe(INSTRUCTIONS);
    expect(r.protocolVersion).toBe('2025-03-26');
  });

  it('answers with a default protocol version when the client names none', async () => {
    const res = await new Harness().request('initialize');
    expect((res.result as { protocolVersion: string }).protocolVersion).toBe('2024-11-05');
  });

  it('lists reply and report', async () => {
    const res = await new Harness().request('tools/list');
    expect((res.result as { tools: { name: string }[] }).tools.map((t) => t.name)).toEqual(['reply', 'report']);
  });

  it('answers ping, rejects unknown requests, survives bad lines and ignores unknown notifications', async () => {
    const h = new Harness();
    expect((await h.request('ping')).result).toEqual({});
    expect((await h.request('resources/list')).error?.code).toBe(-32601);
    await h.server.mcp.handleLine('not json');
    expect(h.out.at(-1)?.error?.code).toBe(-32700);
    await h.server.mcp.handleLine('   ');
    await h.claudeNotifies('notifications/cancelled', { requestId: 1 });
    expect(h.out).toHaveLength(3);
  });

  it('reports an internal error instead of dying when a handler throws', async () => {
    const h = await connected();
    vi.spyOn(h.server.hub!, 'sendCall').mockImplementation(() => {
      throw new Error('boom');
    });
    await h.server.mcp.handleLine(JSON.stringify({ jsonrpc: '2.0', id: 99, method: 'tools/call', params: { name: 'reply', arguments: { text: 'x' } } }));
    expect(h.out.find((m) => m.id === 99)?.error?.code).toBe(-32603);
    expect(h.logs.some((l) => l.includes('boom'))).toBe(true);
    // a notification that throws is logged, and nothing is answered
    vi.spyOn(h.server.hub!, 'sendPermissionRequest').mockImplementation(() => {
      throw new Error('bang');
    });
    await h.claudeNotifies('notifications/claude/channel/permission_request', { request_id: 'abcde', tool_name: 'Bash', description: 'x' });
    expect(h.logs.some((l) => l.includes('bang'))).toBe(true);
    expect((await h.request('ping')).result).toEqual({});
  });
});

describe('connecting', () => {
  it('says hello with the session id and the secret, then reports the role', async () => {
    const h = await connected();
    expect(h.hub.hellos).toEqual([{ type: 'hello', session_id: SESSION_ID, secret: SECRET, plugin_version: '0.1.0' }]);
    expect(h.server.hub?.state).toBe('ready');
    expect(h.server.hub?.welcome?.role).toBe('worker');
  });

  it('runs without a hub when there is no config, and its tools say so', async () => {
    const h = new Harness({ config: undefined, configProblem: 'The hub secret is missing.' });
    h.server.start();
    await h.handshake();
    const res = await h.callTool('reply', { text: 'hi' });
    expect(res.result?.isError).toBe(true);
    expect(text(res)).toContain('The hub secret is missing.');
    expect(h.hub.attempts).toBe(0);
    await h.claudeNotifies('notifications/claude/channel/permission_request', { request_id: 'abcde', tool_name: 'Bash', description: 'x' });
    expect(h.logs.some((l) => l.includes('Answer it in the terminal'))).toBe(true);
    h.server.stop();
  });

  it('says it is not configured when no reason is known', async () => {
    const h = new Harness({ config: undefined });
    expect(text(await h.callTool('reply', { text: 'hi' }))).toContain('not configured');
    expect(h.logs[0]).toContain('No configuration');
  });

  it('retries with backoff 1 s, 2 s, 4 s … capped at 30 s when the hub is down at start', async () => {
    const h = new Harness();
    h.hub.down = true;
    h.server.start();
    await flush();
    const times: number[] = [Date.now()];
    let last = h.hub.attempts;
    const t0 = Date.now();
    const seen: number[] = [];
    for (let i = 0; i < 900_000 && seen.length < 8; i += 500) {
      await vi.advanceTimersByTimeAsync(500);
      if (h.hub.attempts !== last) {
        seen.push(Date.now() - t0);
        last = h.hub.attempts;
      }
    }
    void times;
    // attempts at 1, 3, 7, 15, 31, 61 (cap 30), 91, 121 seconds
    expect(seen).toEqual([1000, 3000, 7000, 15_000, 31_000, 61_000, 91_000, 121_000]);
  });

  it('starts over at 1 s once the hub answers', async () => {
    const h = new Harness();
    h.hub.down = true;
    h.server.start();
    await vi.advanceTimersByTimeAsync(3000); // two failures
    h.hub.down = false;
    await vi.advanceTimersByTimeAsync(4000);
    expect(h.server.hub?.state).toBe('ready');
    h.hub.drop();
    await flush();
    const before = h.hub.attempts;
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.hub.attempts).toBe(before + 1);
  });

  it('keeps going when opening the socket throws', async () => {
    const h = new Harness();
    let n = 0;
    const real = h.hub.createSocket;
    (h.server.hub as unknown as { o: { createSocket: (u: string) => unknown } }).o.createSocket = (u) => {
      if (++n === 1) throw new Error('no route');
      return real(u);
    };
    h.server.start();
    await vi.advanceTimersByTimeAsync(1500);
    expect(h.logs.some((l) => l.includes('no route'))).toBe(true);
    expect(h.server.hub?.state).toBe('ready');
  });

  it('sends resume_after with the last delivered seq when it reconnects', async () => {
    const h = await connected();
    h.hub.push({ type: 'user_message', seq: 7, message_id: 'm', conversation_id: 'c', text: 'hi' });
    await flush();
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.hub.hellos.at(-1)).toMatchObject({ resume_after: 7 });
  });

  it('reconnects when the hub goes quiet, and pings in between', async () => {
    const h = await connected();
    h.hub.onFrame = (f) => (f.type === 'ping' ? { type: 'pong' } : undefined);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(h.hub.of('ping')).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(40_000);
    expect(h.hub.attempts).toBe(1); // pongs keep it alive
    h.hub.onFrame = () => undefined; // now silence
    await vi.advanceTimersByTimeAsync(100_000);
    expect(h.hub.attempts).toBeGreaterThan(1);
    expect(h.logs.some((l) => l.includes('went quiet'))).toBe(true);
  });

  it('ignores frames from a socket it already left', async () => {
    const h = await connected();
    const old = h.hub.current;
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    old.onmessage?.({ data: JSON.stringify({ type: 'user_message', seq: 1, message_id: 'm', conversation_id: 'c', text: 'stale' }) });
    old.onclose?.({});
    expect(h.notifications('notifications/claude/channel')).toHaveLength(0);
    expect(h.server.hub?.state).toBe('ready');
  });

  it('logs and ignores hub frames it cannot read', async () => {
    const h = await connected();
    const s = h.hub.current;
    s.onmessage?.({ data: 'nope' });
    s.onmessage?.({ data: JSON.stringify({ type: 'mystery' }) });
    s.onmessage?.({ data: 'null' });
    s.onmessage?.({ data: JSON.stringify({ type: 'pong' }) });
    s.onmessage?.({ data: JSON.stringify({ type: 'call_ack', call_id: 'c_unknown' }) });
    s.onerror?.({});
    expect(h.logs.filter((l) => l.startsWith('Ignored'))).toHaveLength(3);
    expect(h.server.hub?.state).toBe('ready');
  });
});

describe('inbound messages', () => {
  it('turns a user_message into a channel notification and acks it', async () => {
    const h = await connected();
    h.hub.push({ type: 'user_message', seq: 41, message_id: 'msg_01J9ZQZ1', conversation_id: 'cnv_main_marcel', text: 'Can you fix the flaky login test?' });
    await flush();
    expect(h.notifications('notifications/claude/channel')[0]?.params).toEqual({
      content: 'Can you fix the flaky login test?',
      meta: { kind: 'user_message', message_id: 'msg_01J9ZQZ1', conversation_id: 'cnv_main_marcel' },
    });
    expect(h.hub.of('ack')).toEqual([{ type: 'ack', seq: 41 }]);
  });

  it('turns a steer into a channel notification with task_id, note_id and author', async () => {
    const h = await connected();
    h.hub.push({ type: 'steer', seq: 2, task_id: 'tsk_01J9ZQ6', note_id: 'note_1', author: 'user', text: 'Also named volumes.' });
    await flush();
    expect(h.notifications('notifications/claude/channel')[0]?.params).toEqual({
      content: 'Also named volumes.',
      meta: { kind: 'steer', task_id: 'tsk_01J9ZQ6', note_id: 'note_1', author: 'user' },
    });
  });

  it('turns a system_event into a notification, with data as a fenced JSON block', async () => {
    const h = await connected();
    h.hub.push({ type: 'system_event', seq: 3, kind: 'task_failed', text: 'A task failed.', task_id: 'tsk_1', data: { reason: 'tests' } });
    h.hub.push({ type: 'system_event', seq: 4, kind: 'rollover', text: 'Rolling over.' });
    await flush();
    const [a, b] = h.notifications('notifications/claude/channel');
    expect(a?.params).toEqual({
      content: 'A task failed.\n\n```json\n{\n  "reason": "tests"\n}\n```',
      meta: { kind: 'system_event', event: 'task_failed', task_id: 'tsk_1' },
    });
    expect(b?.params).toEqual({ content: 'Rolling over.', meta: { kind: 'system_event', event: 'rollover' } });
  });

  it('uses meta keys that are identifiers only', async () => {
    const h = await connected();
    h.hub.push({ type: 'steer', seq: 1, task_id: 't', note_id: 'n', author: 'marcel', text: 'x' });
    h.hub.push({ type: 'system_event', seq: 2, kind: 'digest_due', text: 'x', task_id: 't' });
    await flush();
    for (const n of h.notifications('notifications/claude/channel')) {
      for (const key of Object.keys((n.params as { meta: object }).meta)) expect(key).toMatch(/^[A-Za-z0-9_]+$/);
    }
  });

  it('ignores a seq it already delivered, but acks it again', async () => {
    const h = await connected();
    const frame = { type: 'user_message', seq: 5, message_id: 'm', conversation_id: 'c', text: 'once' } as const;
    h.hub.push(frame);
    await flush();
    h.hub.push(frame);
    await flush();
    expect(h.notifications('notifications/claude/channel')).toHaveLength(1);
    expect(h.hub.of('ack')).toEqual([{ type: 'ack', seq: 5 }, { type: 'ack', seq: 5 }]);
  });

  it('holds frames, unacked, until Claude Code has finished its handshake, then delivers them in order', async () => {
    const h = new Harness();
    h.server.start();
    await flush();
    h.hub.push({ type: 'user_message', seq: 2, message_id: 'm2', conversation_id: 'c', text: 'second' });
    h.hub.push({ type: 'user_message', seq: 1, message_id: 'm1', conversation_id: 'c', text: 'first' });
    await flush();
    expect(h.notifications()).toHaveLength(0);
    expect(h.hub.of('ack')).toHaveLength(0);
    await h.handshake();
    expect(h.notifications('notifications/claude/channel').map((n) => (n.params as { content: string }).content)).toEqual(['first', 'second']);
    expect(h.hub.of('ack').map((a) => a.seq)).toEqual([1, 2]);
  });
});

describe('reply', () => {
  it('returns when the hub’s call_ack arrives', async () => {
    const h = await connected();
    h.hub.onFrame = (f) => (f.type === 'reply' ? { type: 'call_ack', call_id: f.call_id } : undefined);
    const res = await h.callTool('reply', { text: 'On it.', in_reply_to: 'msg_1' });
    expect(res.result?.isError).toBeUndefined();
    expect(text(res)).toBe('Sent.');
    const [frame] = h.hub.of('reply');
    expect(frame).toMatchObject({ text: 'On it.', in_reply_to: 'msg_1' });
    expect(frame?.call_id).toMatch(/^c_/);
    expect(h.server.hub?.pendingCallCount).toBe(0);
  });

  it('returns "queued" after 10 s without an ack, and keeps the call', async () => {
    const h = await connected();
    let done = false;
    const p = h.callTool('reply', { text: 'hello' }).then((r) => ((done = true), r));
    await vi.advanceTimersByTimeAsync(9_999);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const res = await p;
    expect(text(res)).toMatch(/^Queued/);
    expect(res.result?.isError).toBeUndefined();
    expect(h.server.hub?.pendingCallCount).toBe(1);
  });

  it('re-sends a reply that never got its call_ack after a reconnect, with the same call_id', async () => {
    const h = await connected();
    const p = h.callTool('reply', { text: 'did it' });
    await flush();
    const first = h.hub.of('reply')[0]!;
    h.hub.drop(); // the hub went away before acking
    await vi.advanceTimersByTimeAsync(10_000);
    expect((await p).result?.content?.[0]?.text).toMatch(/^Queued/);
    const replies = h.hub.of('reply');
    expect(replies).toHaveLength(2);
    expect(replies[1]).toEqual(first);
    expect(h.server.hub?.pendingCallCount).toBe(1); // still no ack: it stays for the next reconnect
    h.hub.push({ type: 'call_ack', call_id: first.call_id, duplicate: true });
    await flush();
    expect(h.server.hub?.pendingCallCount).toBe(0);
  });

  it('does not send the same call again once it is acked', async () => {
    const h = await connected();
    h.hub.onFrame = (f) => (f.type === 'reply' ? { type: 'call_ack', call_id: f.call_id } : undefined);
    await h.callTool('reply', { text: 'one' });
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.hub.of('reply')).toHaveLength(1);
  });

  it('returns at once with "queued" while the hub is down, and sends it when the hub is back', async () => {
    const h = new Harness();
    h.hub.down = true;
    h.server.start();
    await h.handshake();
    await flush();
    const res = await h.callTool('reply', { text: 'anyone there?' });
    expect(text(res)).toMatch(/^Queued: Marcel is connecting/);
    expect(res.result?.isError).toBeUndefined();
    h.hub.down = false;
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.hub.of('reply')).toHaveLength(1);
  });

  it('copes when the hub drops in the middle of a call and the ack comes after the reconnect', async () => {
    const h = await connected();
    const p = h.callTool('reply', { text: 'mid-call' });
    await flush();
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000); // reconnected: the call is sent again
    expect(h.hub.of('reply')).toHaveLength(2);
    h.hub.push({ type: 'call_ack', call_id: h.hub.of('reply')[0]!.call_id });
    const res = await p;
    expect(text(res)).toBe('Sent.');
  });

  it('refuses empty text and bad arguments, and sends nothing', async () => {
    const h = await connected();
    expect(text(await h.callTool('reply', { text: '  ' }))).toMatch(/non-empty/);
    expect(text(await h.callTool('reply', {}))).toMatch(/non-empty/);
    expect(text(await h.callTool('reply', { text: 'ok', in_reply_to: 4 }))).toMatch(/in_reply_to/);
    expect(h.hub.of('reply')).toHaveLength(0);
  });

  it('refuses an unknown tool', async () => {
    const h = await connected();
    const res = await h.callTool('rm_rf', {});
    expect(res.result?.isError).toBe(true);
    expect(text(res)).toMatch(/Unknown tool/);
  });
});

describe('report', () => {
  const ack = (h: Harness) => {
    h.hub.onFrame = (f) => (f.type === 'report' ? { type: 'call_ack', call_id: f.call_id } : undefined);
  };

  it('sends each kind and returns on the ack', async () => {
    const h = await connected();
    ack(h);
    const pr = { kind: 'pr', title: 'Fix login', url: 'https://github.com/shbunder/marcel/pull/9' };
    const file = { kind: 'file', title: 'Notes', path: '/home/shbunder/marcel/workspaces/scratch/notes.md' };
    const calls = [
      { kind: 'started' },
      { kind: 'progress', text: 'Tests are running.' },
      { kind: 'artifact', artifact: pr },
      { kind: 'done', summary: 'Fixed.', artifacts: [pr, file] },
      { kind: 'failed', summary: 'The tests still fail.' },
    ];
    for (const args of calls) expect(text(await h.callTool('report', args))).toBe('Sent.');
    expect(h.hub.of('report').map((r) => r.kind)).toEqual(['started', 'progress', 'artifact', 'done', 'failed']);
    expect(h.hub.of('report')[3]?.artifacts).toEqual([pr, file]);
  });

  it('refuses what the contract refuses, before it reaches the hub', async () => {
    const h = await connected();
    ack(h);
    const bad: [Record<string, unknown>, RegExp][] = [
      [{}, /"kind"/],
      [{ kind: 'nope' }, /"kind"/],
      [{ kind: 'progress' }, /progress report needs/],
      [{ kind: 'artifact' }, /needs "artifact"/],
      [{ kind: 'done' }, /done report needs "summary"/],
      [{ kind: 'failed', summary: '' }, /failed report needs/],
      [{ kind: 'started', text: 5 }, /"text" must be a string/],
      [{ kind: 'started', summary: 5 }, /"summary" must be a string/],
      [{ kind: 'artifact', artifact: 'x' }, /must be an object/],
      [{ kind: 'artifact', artifact: [] }, /must be an object/],
      [{ kind: 'artifact', artifact: { kind: 'zip', title: 't' } }, /one of/],
      [{ kind: 'artifact', artifact: { kind: 'pr' } }, /"title"/],
      [{ kind: 'artifact', artifact: { kind: 'pr', title: 't' } }, /pr artifact needs a "url"/],
      [{ kind: 'artifact', artifact: { kind: 'link', title: 't', url: 'not a url' } }, /full URL/],
      [{ kind: 'artifact', artifact: { kind: 'doc', title: 't' } }, /doc artifact needs a "path"/],
      [{ kind: 'artifact', artifact: { kind: 'doc', title: 't', path: 3 } }, /"path" must be a string/],
      [{ kind: 'started', artifacts: 'x' }, /must be a list/],
      [{ kind: 'started', artifacts: [{ kind: 'pr', title: 't' }] }, /needs a "url"/],
    ];
    for (const [args, pattern] of bad) {
      const res = await h.callTool('report', args);
      expect(res.result?.isError, JSON.stringify(args)).toBe(true);
      expect(text(res)).toMatch(pattern);
    }
    expect(h.hub.of('report')).toHaveLength(0);
  });

  it('keeps only the fields the contract has', async () => {
    const h = await connected();
    ack(h);
    await h.callTool('report', { kind: 'started', secret_extra: 'x' });
    expect(Object.keys(h.hub.of('report')[0]!).sort()).toEqual(['call_id', 'kind', 'type']);
  });
});

describe('permission relay', () => {
  const request = { request_id: 'qiwxo', tool_name: 'Bash', description: 'Write and print UTC date', input_preview: '{ "command": "date -u" }' };

  it('relays a prompt to the hub and the owner’s answer back to Claude Code', async () => {
    const h = await connected();
    await h.claudeNotifies('notifications/claude/channel/permission_request', request);
    expect(h.hub.of('permission_request')).toEqual([{ type: 'permission_request', ...request }]);
    h.hub.push({ type: 'permission_decision', seq: 44, request_id: 'qiwxo', behavior: 'allow' });
    await flush();
    expect(h.notifications('notifications/claude/channel/permission')[0]?.params).toEqual({ request_id: 'qiwxo', behavior: 'allow' });
    expect(h.hub.of('ack')).toEqual([{ type: 'ack', seq: 44 }]);
    expect(h.server.hub?.pendingPermissionCount).toBe(0);
  });

  it('relays a denial', async () => {
    const h = await connected();
    await h.claudeNotifies('notifications/claude/channel/permission_request', request);
    h.hub.push({ type: 'permission_decision', seq: 1, request_id: 'qiwxo', behavior: 'deny' });
    await flush();
    expect(h.notifications('notifications/claude/channel/permission')[0]?.params).toEqual({ request_id: 'qiwxo', behavior: 'deny' });
  });

  it('fills in what Claude Code leaves out', async () => {
    const h = await connected();
    await h.claudeNotifies('notifications/claude/channel/permission_request', { request_id: 'abcde' });
    expect(h.hub.of('permission_request')[0]).toEqual({ type: 'permission_request', request_id: 'abcde', tool_name: 'unknown', description: '' });
  });

  it('drops a request id the contract would refuse, and says so', async () => {
    const h = await connected();
    for (const id of ['abcdl', 'ABCDE', 'abcd', 7, undefined]) {
      await h.claudeNotifies('notifications/claude/channel/permission_request', { ...request, request_id: id });
    }
    expect(h.hub.of('permission_request')).toHaveLength(0);
    expect(h.logs.filter((l) => l.includes('bad id'))).toHaveLength(5);
  });

  it('re-sends a pending prompt after a reconnect, and the decision then still reaches Claude Code', async () => {
    const h = await connected();
    await h.claudeNotifies('notifications/claude/channel/permission_request', request);
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.hub.of('permission_request')).toHaveLength(2);
    expect(h.hub.of('permission_request')[1]).toEqual(h.hub.of('permission_request')[0]);
    h.hub.push({ type: 'permission_decision', seq: 1, request_id: 'qiwxo', behavior: 'allow' });
    await flush();
    expect(h.notifications('notifications/claude/channel/permission')).toHaveLength(1);
    // answered: it is not sent again on the next reconnect
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.hub.of('permission_request')).toHaveLength(2);
  });

  it('holds a prompt raised while the hub is down and sends it after the hello', async () => {
    const h = new Harness();
    h.hub.down = true;
    h.server.start();
    await h.handshake();
    await flush();
    await h.claudeNotifies('notifications/claude/channel/permission_request', request);
    expect(h.hub.of('permission_request')).toHaveLength(0);
    h.hub.down = false;
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.hub.of('permission_request')).toHaveLength(1);
  });

  it('passes on a decision for a prompt it does not remember', async () => {
    const h = await connected();
    h.hub.push({ type: 'permission_decision', seq: 1, request_id: 'zzzzz', behavior: 'deny' });
    await flush();
    expect(h.notifications('notifications/claude/channel/permission')).toHaveLength(1);
  });
});

describe('rejections', () => {
  const reject = (reason: 'bad_secret' | 'plugin_too_old' | 'unknown_session', message?: string) => ({ type: 'rejected' as const, reason, ...(message ? { message } : {}) });

  for (const reason of ['bad_secret', 'plugin_too_old'] as const) {
    it(`${reason} is logged once and never retried; the tools say Marcel is unreachable`, async () => {
      const h = new Harness();
      h.hub.helloReply = reject(reason, 'Details.');
      h.server.start();
      await h.handshake();
      const pending = h.callTool('reply', { text: 'before' });
      await flush();
      await vi.advanceTimersByTimeAsync(600_000);
      await pending;
      expect(h.hub.attempts).toBe(1);
      expect(h.server.hub?.state).toBe('stopped');
      expect(h.server.hub?.stopReason).toBe(reason);
      expect(h.logs.filter((l) => l.includes('Not retrying'))).toHaveLength(1);
      const res = await h.callTool('report', { kind: 'started' });
      expect(res.result?.isError).toBe(true);
      expect(text(res)).toMatch(/Marcel is unreachable/);
      expect(h.hub.of('reply')).toHaveLength(0);
    });
  }

  it('names the cause in the unreachable message', async () => {
    const secret = new Harness();
    secret.hub.helloReply = reject('bad_secret');
    secret.server.start();
    await flush();
    expect(text(await secret.callTool('reply', { text: 'x' }))).toMatch(/secret/);
    const old = new Harness();
    old.hub.helloReply = reject('plugin_too_old');
    old.server.start();
    await flush();
    expect(text(await old.callTool('reply', { text: 'x' }))).toMatch(/Update the plugin/);
  });

  it('fails a call that is waiting for its ack when the hub then refuses the session', async () => {
    const h = await connected();
    const p = h.callTool('reply', { text: 'in flight' });
    await flush();
    h.hub.helloReply = reject('bad_secret');
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(10_000);
    const res = await p;
    expect(res.result?.isError).toBe(true);
    expect(text(res)).toMatch(/Marcel is unreachable/);
    expect(h.server.hub?.pendingCallCount).toBe(0);
  });

  it('retries unknown_session every 5 s for 2 minutes, then logs it and stops', async () => {
    const h = new Harness();
    h.hub.helloReply = reject('unknown_session', 'No task is waiting.');
    h.server.start();
    await vi.advanceTimersByTimeAsync(4_999);
    expect(h.hub.attempts).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.hub.attempts).toBe(2);
    await vi.advanceTimersByTimeAsync(115_000); // t = 120 s
    expect(h.hub.attempts).toBe(25);
    expect(h.server.hub?.state).toBe('stopped');
    expect(h.logs.some((l) => l.includes('Giving up'))).toBe(true);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(h.hub.attempts).toBe(25);
    expect(h.server.hub?.stopReason).toBe('unknown_session');
  });

  it('gets in when the runner reports the session during the retry window', async () => {
    const h = new Harness();
    h.hub.helloReply = reject('unknown_session');
    h.server.start();
    await vi.advanceTimersByTimeAsync(12_000);
    h.hub.helloReply = { type: 'welcome', role: 'brain', agent_id: 'agt_marcel', conversation_id: 'cnv_main_marcel' };
    await vi.advanceTimersByTimeAsync(5_000);
    expect(h.server.hub?.state).toBe('ready');
    // and the clock starts afresh for a later rejection
    h.hub.helloReply = reject('unknown_session');
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(110_000);
    expect(h.server.hub?.state).not.toBe('stopped');
  });

  it('stop() ends reconnecting and clears open calls', async () => {
    const h = await connected();
    const p = h.callTool('reply', { text: 'bye' });
    await flush();
    h.server.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.hub.attempts).toBe(1);
    expect((await p).result?.isError).toBe(true);
    expect(text(await h.callTool('reply', { text: 'again' }))).toMatch(/shutting down/);
    h.server.stop(); // twice is fine
  });
});

describe('queued wording', () => {
  it('says "reconnecting" once the hub has been reached before', async () => {
    const h = await connected();
    h.hub.down = true;
    h.hub.drop();
    await flush();
    expect(text(await h.callTool('reply', { text: 'later' }))).toMatch(/^Queued: Marcel is reconnecting/);
  });
});

describe('hello timeout', () => {
  it('closes a connection the hub accepts but never answers, logs it, and reconnects with backoff', async () => {
    const h = new Harness();
    h.hub.helloReply = 'silent';
    h.server.start();
    await vi.advanceTimersByTimeAsync(14_999);
    expect(h.hub.attempts).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.hub.sockets[0]?.closedByClient).toBe(true);
    expect(h.logs.some((l) => l.includes('did not answer hello'))).toBe(true);
    expect(h.server.hub?.state).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(1000); // backoff 1 s
    expect(h.hub.attempts).toBe(2);
    // the second silence waits 2 s before the next try
    await vi.advanceTimersByTimeAsync(15_000 + 1999);
    expect(h.hub.attempts).toBe(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.hub.attempts).toBe(3);
  });

  it('does not fire once the hub answers', async () => {
    const h = await connected();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.logs.some((l) => l.includes('did not answer hello'))).toBe(false);
    expect(h.hub.attempts).toBe(1);
  });

  it('does not fire after a rejection or a close', async () => {
    const h = new Harness();
    h.hub.helloReply = { type: 'rejected', reason: 'bad_secret' };
    h.server.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.logs.some((l) => l.includes('did not answer hello'))).toBe(false);
    const g = new Harness();
    g.hub.helloReply = 'silent';
    g.server.start();
    await flush();
    g.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(g.logs.some((l) => l.includes('did not answer hello'))).toBe(false);
  });
});

describe('settle delay after initialized', () => {
  it('holds a frame queued at hello for 500 ms after notifications/initialized, then delivers and acks it', async () => {
    const h = new Harness({ settleMs: 500 });
    h.server.start();
    h.hub.push({ type: 'user_message', seq: 1, message_id: 'm', conversation_id: 'c', text: 'queued at hello' }, undefined);
    await h.handshake();
    await flush();
    await vi.advanceTimersByTimeAsync(499);
    expect(h.notifications()).toHaveLength(0);
    expect(h.hub.of('ack')).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.notifications('notifications/claude/channel')).toHaveLength(1);
    expect(h.hub.of('ack')).toEqual([{ type: 'ack', seq: 1 }]);
  });

  it('uses 500 ms by default, and delivers at once afterwards', async () => {
    const h = new Harness();
    (h.server as unknown as { deps: { settleMs?: number } }).deps.settleMs = undefined;
    h.server.start();
    await h.handshake();
    await flush();
    h.hub.push({ type: 'user_message', seq: 1, message_id: 'm', conversation_id: 'c', text: 'early' });
    await vi.advanceTimersByTimeAsync(499);
    expect(h.notifications()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.notifications()).toHaveLength(1);
    h.hub.push({ type: 'user_message', seq: 2, message_id: 'm2', conversation_id: 'c', text: 'later' });
    await flush();
    expect(h.notifications()).toHaveLength(2);
  });

  it('does not deliver after the server is stopped during the settle delay', async () => {
    const h = new Harness({ settleMs: 500 });
    h.server.start();
    await h.handshake();
    await flush();
    h.hub.push({ type: 'user_message', seq: 1, message_id: 'm', conversation_id: 'c', text: 'x' });
    await flush();
    h.server.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.notifications()).toHaveLength(0);
  });
});

describe('giving up says what was dropped', () => {
  it('bad_secret: counts queued calls and pending prompts', async () => {
    const h = await connected();
    void h.callTool('reply', { text: 'one' });
    void h.callTool('reply', { text: 'two' });
    await h.claudeNotifies('notifications/claude/channel/permission_request', { request_id: 'abcde', tool_name: 'Bash', description: 'x' });
    await flush();
    h.hub.helloReply = { type: 'rejected', reason: 'bad_secret' };
    h.hub.drop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.logs).toContain('Gave up on the hub (bad_secret). Dropped 2 queued calls and 1 pending permission prompt.');
  });

  it('plugin_too_old with nothing queued says zero, singular where it should', async () => {
    const h = new Harness();
    h.hub.helloReply = { type: 'rejected', reason: 'plugin_too_old' };
    h.server.start();
    await flush();
    expect(h.logs).toContain('Gave up on the hub (plugin_too_old). Dropped 0 queued calls and 0 pending permission prompts.');
  });

  it('unknown_session: says it at the end of the window, with one call', async () => {
    const h = new Harness();
    h.hub.helloReply = { type: 'rejected', reason: 'unknown_session' };
    h.server.start();
    await h.handshake();
    void h.callTool('reply', { text: 'waiting' });
    await vi.advanceTimersByTimeAsync(121_000);
    expect(h.logs).toContain('Gave up on the hub (unknown_session). Dropped 1 queued call and 0 pending permission prompts.');
  });
});
