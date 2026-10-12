// The built server as Claude Code runs it: a child process on stdio, talking to a real WebSocket server.
import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, type WebSocket } from 'ws';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { assertToHub, assertToSession, SECRET, SESSION_ID } from './harness.js';

const root = fileURLToPath(new URL('..', import.meta.url));
let dist: string;
let dataDir: string;
let wss: WebSocketServer;
let port: number;
const children: ChildProcessWithoutNullStreams[] = [];

beforeAll(async () => {
  dist = mkdtempSync(join(tmpdir(), 'marcel-dist-'));
  execFileSync('npx', ['tsc', '-p', 'tsconfig.build.json', '--outDir', dist], { cwd: root, stdio: 'pipe' });
  writeFileSync(join(dist, 'package.json'), '{"type":"module"}');
  dataDir = mkdtempSync(join(tmpdir(), 'marcel-data-'));
  writeFileSync(join(dataDir, 'hub-secret'), SECRET);
  wss = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await new Promise((r) => wss.once('listening', r));
  port = (wss.address() as { port: number }).port;
});

afterAll(() => {
  for (const c of children) c.kill();
  wss.close();
});

function startServer(env: Record<string, string>) {
  const child = spawn('node', [join(dist, 'main.js')], {
    env: { PATH: process.env.PATH ?? '', CLAUDE_PLUGIN_DATA: dataDir, MARCEL_HUB_URL: `ws://127.0.0.1:${port}/channel`, ...env },
  });
  children.push(child);
  const lines: Record<string, unknown>[] = [];
  const waiters: (() => void)[] = [];
  createInterface({ input: child.stdout }).on('line', (l) => {
    lines.push(JSON.parse(l) as Record<string, unknown>);
    waiters.splice(0).forEach((w) => w());
  });
  const stderr: string[] = [];
  child.stderr.on('data', (d: Buffer) => stderr.push(String(d)));
  const send = (msg: object) => child.stdin.write(`${JSON.stringify(msg)}\n`);
  const waitFor = async (pred: (m: Record<string, unknown>) => boolean) => {
    for (let i = 0; i < 200; i++) {
      const hit = lines.find(pred);
      if (hit) return hit;
      await new Promise<void>((r) => (waiters.push(r), setTimeout(r, 50)));
    }
    throw new Error(`timed out; stdout so far: ${JSON.stringify(lines)} stderr: ${stderr.join('')}`);
  };
  return { child, send, waitFor, stderr };
}

const nextConnection = () => new Promise<WebSocket>((r) => wss.once('connection', r));
const nextFrame = (ws: WebSocket) =>
  new Promise<Record<string, unknown>>((r) =>
    ws.once('message', (d) => {
      const f: unknown = JSON.parse(String(d));
      assertToHub(f);
      r(f as unknown as Record<string, unknown>);
    }),
  );
const sendFrame = (ws: WebSocket, frame: object) => {
  assertToSession(frame);
  ws.send(JSON.stringify(frame));
};

describe('built server over stdio and a real WebSocket', () => {
  it('says hello, receives a message as a channel notification, and a reply goes out and is acked', async () => {
    const conn = nextConnection();
    const s = startServer({ CLAUDE_CODE_SESSION_ID: SESSION_ID });
    const ws = await conn;
    const hello = await nextFrame(ws);
    expect(hello).toEqual({ type: 'hello', session_id: SESSION_ID, secret: SECRET, plugin_version: '0.1.0' });
    sendFrame(ws, { type: 'welcome', role: 'brain', agent_id: 'agt_marcel', conversation_id: 'cnv_main_marcel' });

    s.send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26' } });
    const init = await s.waitFor((m) => m.id === 1);
    expect((init.result as { capabilities: { experimental: object } }).capabilities.experimental).toHaveProperty('claude/channel/permission');
    s.send({ jsonrpc: '2.0', method: 'notifications/initialized' });

    const ack = nextFrame(ws);
    sendFrame(ws, { type: 'user_message', seq: 1, message_id: 'msg_1', conversation_id: 'cnv_main_marcel', text: 'Hello Marcel' });
    const note = await s.waitFor((m) => m.method === 'notifications/claude/channel');
    expect(note.params).toEqual({ content: 'Hello Marcel', meta: { kind: 'user_message', message_id: 'msg_1', conversation_id: 'cnv_main_marcel' } });
    expect(await ack).toEqual({ type: 'ack', seq: 1 });

    const replyFrame = nextFrame(ws);
    s.send({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'reply', arguments: { text: 'Hi.', in_reply_to: 'msg_1' } } });
    const reply = await replyFrame;
    expect(reply).toMatchObject({ type: 'reply', text: 'Hi.', in_reply_to: 'msg_1' });
    sendFrame(ws, { type: 'call_ack', call_id: reply.call_id });
    const done = await s.waitFor((m) => m.id === 2);
    expect((done.result as { content: { text: string }[] }).content[0]?.text).toBe('Sent.');

    // a permission prompt goes out and the decision comes back
    const req = nextFrame(ws);
    s.send({ jsonrpc: '2.0', method: 'notifications/claude/channel/permission_request', params: { request_id: 'qiwxo', tool_name: 'Bash', description: 'date' } });
    expect(await req).toMatchObject({ type: 'permission_request', request_id: 'qiwxo' });
    sendFrame(ws, { type: 'permission_decision', seq: 2, request_id: 'qiwxo', behavior: 'allow' });
    const decision = await s.waitFor((m) => m.method === 'notifications/claude/channel/permission');
    expect(decision.params).toEqual({ request_id: 'qiwxo', behavior: 'allow' });

    s.child.stdin.end();
    await new Promise((r) => s.child.once('exit', r));
  });

  it('starts without a hub connection and still answers MCP when its session id is missing', async () => {
    const s = startServer({});
    s.send({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'reply', arguments: { text: 'x' } } });
    const res = await s.waitFor((m) => m.id === 1);
    expect((res.result as { isError: boolean }).isError).toBe(true);
    expect(s.stderr.join('')).toContain('[marcel-channel] Not connecting to the hub.');
    s.child.stdin.end();
  });
});
