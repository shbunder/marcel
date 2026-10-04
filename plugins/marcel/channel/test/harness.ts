import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Ajv2020 } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import type { Config } from '../src/config.js';
import type { ToHub, ToSession } from '../src/frames.js';
import type { SocketLike } from '../src/hub.js';
import { ChannelServer } from '../src/server.js';

const SCHEMA_PATH = fileURLToPath(new URL('../../../../contracts/channel.schema.json', import.meta.url));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats.default(ajv);
ajv.addSchema(JSON.parse(readFileSync(SCHEMA_PATH, 'utf8')));
const SCHEMA_ID = 'https://marcel.invalid/contracts/channel.schema.json';
const toHub = ajv.getSchema(`${SCHEMA_ID}#/$defs/ToHub`)!;
const toSession = ajv.getSchema(`${SCHEMA_ID}#/$defs/ToSession`)!;

export function assertToHub(frame: unknown): asserts frame is ToHub {
  if (!toHub(frame)) throw new Error(`frame breaks channel.schema.json (ToHub): ${JSON.stringify(frame)} ${ajv.errorsText(toHub.errors)}`);
}
export function assertToSession(frame: unknown): asserts frame is ToSession {
  if (!toSession(frame)) throw new Error(`frame breaks channel.schema.json (ToSession): ${JSON.stringify(frame)} ${ajv.errorsText(toSession.errors)}`);
}

export const SESSION_ID = '3986e1fc-278e-4b56-ba17-ad79a8130252';
export const SECRET = 'hs_9f2c41d07be84a6c9d3e1f5a2b7c8d90e1f2a3b4';
export const CONFIG: Config = { hubUrl: 'ws://hub.test/channel', sessionId: SESSION_ID, secret: SECRET, pluginVersion: '0.1.0' };

export class FakeSocket implements SocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closedByClient = false;
  constructor(private readonly hub: FakeHub) {}
  send(data: string): void {
    this.hub.receive(this, data);
  }
  close(): void {
    this.closedByClient = true;
  }
}

export type HelloReply = ToSession | 'silent';

/** An in-process hub: it checks every frame against the contract, in both directions. */
export class FakeHub {
  sockets: FakeSocket[] = [];
  /** Every frame the server sent, in order, across all connections. */
  received: ToHub[] = [];
  /** Hello frames only. */
  hellos: ToHub[] = [];
  /** `true`: the connection attempt fails (hub down). */
  down = false;
  /** What the hub answers to a hello. */
  helloReply: HelloReply = { type: 'welcome', role: 'worker', agent_id: 'agt_marcel', task_id: 'tsk_01J9ZQ6' };
  /** Called for every non-hello frame; return a frame to answer with. */
  onFrame: (frame: ToHub, socket: FakeSocket) => ToSession | undefined = () => undefined;
  attempts = 0;

  createSocket = (_url: string): FakeSocket => {
    this.attempts += 1;
    const socket = new FakeSocket(this);
    this.sockets.push(socket);
    queueMicrotask(() => {
      if (this.down) socket.onclose?.({});
      else socket.onopen?.({});
    });
    return socket;
  };

  get current(): FakeSocket {
    return this.sockets[this.sockets.length - 1]!;
  }

  receive(socket: FakeSocket, data: string): void {
    const frame: unknown = JSON.parse(data);
    assertToHub(frame);
    this.received.push(frame);
    if (frame.type === 'hello') {
      this.hellos.push(frame);
      if (this.helloReply !== 'silent') this.push(this.helloReply, socket);
      return;
    }
    const answer = this.onFrame(frame, socket);
    if (answer) this.push(answer, socket);
  }

  /** Hub → session. Validated against the contract first. */
  push(frame: ToSession, socket: FakeSocket = this.current): void {
    assertToSession(frame);
    queueMicrotask(() => socket.onmessage?.({ data: JSON.stringify(frame) }));
  }

  /** The connection drops under the server. */
  drop(): void {
    const socket = this.current;
    queueMicrotask(() => socket.onclose?.({}));
  }

  of<T extends ToHub['type']>(type: T): Extract<ToHub, { type: T }>[] {
    return this.received.filter((f): f is Extract<ToHub, { type: T }> => f.type === type);
  }
}

export interface Rpc {
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown> & { content?: { text: string }[]; isError?: boolean };
  error?: { code: number; message: string };
}

/** A fake MCP client (Claude Code) on the other end of the stdio pipe. */
export class Harness {
  readonly hub = new FakeHub();
  readonly out: Rpc[] = [];
  readonly logs: string[] = [];
  readonly server: ChannelServer;
  private nextId = 1;
  private readonly waiters = new Map<number, (msg: Rpc) => void>();

  constructor(opts: { config?: Config | undefined; configProblem?: string; timing?: Record<string, number>; settleMs?: number } = {}) {
    this.server = new ChannelServer({
      config: 'config' in opts ? opts.config : CONFIG,
      configProblem: opts.configProblem,
      write: (line) => {
        const msg = JSON.parse(line) as Rpc;
        this.out.push(msg);
        if (msg.id !== undefined) this.waiters.get(msg.id)?.(msg);
      },
      log: (line) => this.logs.push(line),
      createSocket: this.hub.createSocket,
      hubTiming: opts.timing,
      settleMs: opts.settleMs ?? 0,
    });
  }

  /** Initialize like Claude Code does. */
  async handshake(): Promise<Rpc> {
    const res = await this.request('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'claude-code', version: '2.1.289' } });
    await this.server.mcp.handleLine(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }));
    return res;
  }

  async request(method: string, params: Record<string, unknown> = {}): Promise<Rpc> {
    const id = this.nextId++;
    await this.server.mcp.handleLine(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
    const res = this.out.find((m) => m.id === id);
    if (!res) throw new Error(`no response to ${method}`);
    return res;
  }

  /** Start a tool call without waiting for it to return. */
  callTool(name: string, args: Record<string, unknown>): Promise<Rpc> {
    const id = this.nextId++;
    const done = new Promise<Rpc>((resolve) => this.waiters.set(id, resolve));
    void this.server.mcp.handleLine(JSON.stringify({ jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: args } }));
    return done;
  }

  /** Claude Code sends a notification to the server. */
  async claudeNotifies(method: string, params: Record<string, unknown>): Promise<void> {
    await this.server.mcp.handleLine(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }

  notifications(method?: string): Rpc[] {
    return this.out.filter((m) => m.method && (!method || m.method === method));
  }
}
