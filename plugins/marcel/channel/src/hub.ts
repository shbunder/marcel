import type { Deliverable, Hello, PermissionRequest, RejectReason, Reply, Report, ToHub, ToSession, Welcome } from './frames.js';

/** The slice of the WebSocket API used here; Node's global WebSocket satisfies it. */
export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type HubState = 'connecting' | 'ready' | 'reconnecting' | 'stopped';

export interface HubOptions {
  url: string;
  sessionId: string;
  secret: string;
  pluginVersion: string;
  createSocket: (url: string) => SocketLike;
  onDeliver: (frame: Deliverable) => void;
  onWelcome?: (welcome: Welcome) => void;
  log: (line: string) => void;
  backoffStartMs?: number;
  backoffMaxMs?: number;
  unknownRetryMs?: number;
  unknownWindowMs?: number;
  pingMs?: number;
  ackTimeoutMs?: number;
  helloTimeoutMs?: number;
}

export interface CallOutcome {
  status: 'sent' | 'queued' | 'unreachable';
  text: string;
}

const STOP_TEXT: Record<RejectReason, string> = {
  bad_secret: 'The hub does not accept this session’s secret. A deploy wrote a different one.',
  plugin_too_old: 'The marcel plugin is older than the hub needs. Update the plugin.',
  unknown_session: 'The hub does not know this session.',
};

interface PendingCall {
  frame: Reply | Report;
  settle: (outcome: CallOutcome) => void;
  timer: ReturnType<typeof setTimeout> | undefined;
  settled: boolean;
}

/** One WebSocket to the hub: hello, reconnect with backoff, seq dedupe, and re-send of anything unanswered. */
export class HubClient {
  state: HubState = 'connecting';
  welcome: Welcome | undefined;
  stopReason: RejectReason | undefined;

  private readonly o: Required<Omit<HubOptions, 'onWelcome'>> & Pick<HubOptions, 'onWelcome'>;
  private socket: SocketLike | undefined;
  private lastSeq = 0;
  private failures = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private pingTimer: ReturnType<typeof setInterval> | undefined;
  private lastReceived = 0;
  private unknownSince: number | undefined;
  private rejectedThisSocket = false;
  private ended = false;
  private everReady = false;
  private helloTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly calls = new Map<string, PendingCall>();
  private readonly permissions = new Map<string, PermissionRequest>();

  constructor(opts: HubOptions) {
    this.o = {
      backoffStartMs: 1000,
      backoffMaxMs: 30_000,
      unknownRetryMs: 5000,
      unknownWindowMs: 120_000,
      pingMs: 20_000,
      ackTimeoutMs: 10_000,
      helloTimeoutMs: 15_000,
      ...opts,
    };
  }

  start(): void {
    this.connect();
  }

  /** Shut down for good: no more reconnects, open tool calls answer "unreachable". */
  stop(): void {
    this.ended = true;
    this.teardown();
    this.state = 'stopped';
    this.failAllCalls('Marcel is shutting down.');
  }

  /** Send a reply or report. Resolves on `call_ack`, after the ack timeout, or at once when the hub is not connected. */
  sendCall(frame: Reply | Report): Promise<CallOutcome> {
    if (this.state === 'stopped') {
      return Promise.resolve({ status: 'unreachable', text: this.unreachableText() });
    }
    return new Promise((resolve) => {
      const call: PendingCall = { frame, settle: resolve, timer: undefined, settled: false };
      this.calls.set(frame.call_id, call);
      if (this.state === 'ready') {
        call.timer = setTimeout(() => {
          this.settle(call, { status: 'queued', text: 'Queued: the hub has not confirmed it yet. Marcel will keep trying.' });
        }, this.o.ackTimeoutMs);
        this.send(frame);
      } else {
        this.settle(call, { status: 'queued', text: `Queued: Marcel is ${this.everReady ? 'reconnecting' : 'connecting'}. It will be sent when the connection is up.` });
      }
    });
  }

  /** Relay a permission prompt. It is kept, and re-sent after every reconnect, until a decision arrives. */
  sendPermissionRequest(frame: PermissionRequest): void {
    this.permissions.set(frame.request_id, frame);
    if (this.state === 'ready') this.send(frame);
  }

  /** The decision for this prompt has been handed to Claude Code. */
  forgetPermission(requestId: string): void {
    this.permissions.delete(requestId);
  }

  /** A hub frame has been handed to Claude Code: remember it and tell the hub. */
  markDelivered(seq: number): void {
    this.lastSeq = Math.max(this.lastSeq, seq);
    this.send({ type: 'ack', seq });
  }

  get pendingCallCount(): number {
    return this.calls.size;
  }

  get pendingPermissionCount(): number {
    return this.permissions.size;
  }

  // --- connection -------------------------------------------------------------------------------------------

  private connect(): void {
    if (this.ended) return;
    this.reconnectTimer = undefined;
    this.rejectedThisSocket = false;
    let socket: SocketLike;
    try {
      socket = this.o.createSocket(this.o.url);
    } catch (e) {
      this.o.log(`Could not open the hub connection: ${String(e)}`);
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.lastReceived = Date.now();
      const hello: Hello = {
        type: 'hello',
        session_id: this.o.sessionId,
        secret: this.o.secret,
        plugin_version: this.o.pluginVersion,
      };
      if (this.lastSeq > 0) hello.resume_after = this.lastSeq;
      socket.send(JSON.stringify(hello));
      this.helloTimer = setTimeout(() => this.onHelloTimeout(socket), this.o.helloTimeoutMs);
    };
    socket.onmessage = (ev) => {
      if (this.socket === socket) this.onData(ev.data);
    };
    socket.onerror = () => {
      // A close always follows; reconnecting is decided there.
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = undefined;
      this.clearPing();
      this.clearHelloTimer();
      if (this.rejectedThisSocket || this.ended) return;
      if (this.state === 'ready') this.o.log('Lost the hub connection. Reconnecting.');
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(retryAfterMs?: number): void {
    if (this.ended || this.state === 'stopped') return;
    this.state = 'reconnecting';
    let delay = retryAfterMs;
    if (delay === undefined) {
      delay = Math.min(this.o.backoffStartMs * 2 ** this.failures, this.o.backoffMaxMs);
      this.failures += 1;
    }
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private teardown(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    this.clearPing();
    this.clearHelloTimer();
    this.abandon();
  }

  /** Let go of the current socket without waiting for it to say goodbye. */
  private abandon(): void {
    const socket = this.socket;
    this.socket = undefined;
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      try {
        socket.close();
      } catch {
        // already closed
      }
    }
  }

  private clearHelloTimer(): void {
    if (this.helloTimer) clearTimeout(this.helloTimer);
    this.helloTimer = undefined;
  }

  private onHelloTimeout(socket: SocketLike): void {
    this.helloTimer = undefined;
    if (this.socket !== socket) return;
    this.o.log(`The hub took the connection but did not answer hello in ${Math.round(this.o.helloTimeoutMs / 1000)} s. Reconnecting.`);
    this.abandon();
    this.scheduleReconnect();
  }

  private clearPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = undefined;
  }

  private send(frame: ToHub): boolean {
    if (!this.socket || this.state !== 'ready') return false;
    try {
      this.socket.send(JSON.stringify(frame));
      return true;
    } catch (e) {
      this.o.log(`Could not send a ${frame.type} frame: ${String(e)}`);
      return false;
    }
  }

  // --- frames from the hub ----------------------------------------------------------------------------------

  private onData(data: unknown): void {
    this.lastReceived = Date.now();
    let frame: ToSession;
    try {
      frame = JSON.parse(String(data)) as ToSession;
    } catch {
      this.o.log('Ignored a hub frame that is not JSON.');
      return;
    }
    switch (frame?.type) {
      case 'welcome':
        return this.onWelcome(frame);
      case 'rejected':
        return this.onRejected(frame.reason, frame.message);
      case 'call_ack': {
        const call = this.calls.get(frame.call_id);
        if (call) this.settle(call, { status: 'sent', text: 'Sent.' });
        return;
      }
      case 'pong':
        return;
      case 'user_message':
      case 'steer':
      case 'system_event':
      case 'permission_decision':
        if (frame.seq <= this.lastSeq) {
          // Already delivered: the hub missed our ack. Say it again so it stops re-sending.
          this.send({ type: 'ack', seq: frame.seq });
          return;
        }
        this.o.onDeliver(frame);
        return;
      default:
        this.o.log(`Ignored a hub frame of unknown type: ${String((frame as { type?: unknown } | null)?.type)}`);
    }
  }

  private onWelcome(welcome: Welcome): void {
    this.clearHelloTimer();
    this.state = 'ready';
    this.everReady = true;
    this.welcome = welcome;
    this.failures = 0;
    this.unknownSince = undefined;
    this.clearPing();
    this.pingTimer = setInterval(() => this.onPingTick(), this.o.pingMs);
    this.o.log(`Connected to the hub as ${welcome.role}.`);
    this.o.onWelcome?.(welcome);
    // Anything the hub may not have stored goes again; the hub acks a repeated call_id with `duplicate`.
    for (const call of this.calls.values()) this.send(call.frame);
    for (const request of this.permissions.values()) this.send(request);
  }

  private onRejected(reason: RejectReason, message: string | undefined): void {
    this.clearHelloTimer();
    this.rejectedThisSocket = true;
    const detail = message ? ` ${message}` : '';
    if (reason === 'unknown_session') {
      const now = Date.now();
      this.unknownSince ??= now;
      this.teardown();
      if (now - this.unknownSince >= this.o.unknownWindowMs) {
        this.o.log(`The hub still does not know this session after ${Math.round(this.o.unknownWindowMs / 1000)} s. Giving up.${detail}`);
        this.halt(reason);
        return;
      }
      this.o.log(`The hub does not know this session yet. Trying again in ${Math.round(this.o.unknownRetryMs / 1000)} s.${detail}`);
      this.scheduleReconnect(this.o.unknownRetryMs);
      return;
    }
    this.o.log(`${STOP_TEXT[reason]} Not retrying.${detail}`);
    this.teardown();
    this.halt(reason);
  }

  private halt(reason: RejectReason): void {
    const calls = this.calls.size;
    const prompts = this.permissions.size;
    this.o.log(`Gave up on the hub (${reason}). Dropped ${calls} queued ${calls === 1 ? 'call' : 'calls'} and ${prompts} pending permission ${prompts === 1 ? 'prompt' : 'prompts'}.`);
    this.state = 'stopped';
    this.stopReason = reason;
    this.permissions.clear();
    this.failAllCalls(this.unreachableText());
  }

  private onPingTick(): void {
    if (Date.now() - this.lastReceived > this.o.pingMs * 3) {
      this.o.log('The hub went quiet. Reconnecting.');
      this.clearPing();
      this.abandon();
      this.scheduleReconnect();
      return;
    }
    this.send({ type: 'ping' });
  }

  // --- tool calls -------------------------------------------------------------------------------------------

  private settle(call: PendingCall, outcome: CallOutcome): void {
    if (call.timer) clearTimeout(call.timer);
    call.timer = undefined;
    if (!call.settled) {
      call.settled = true;
      call.settle(outcome);
    }
    // Only an ack ends the call. A queued call stays here to be re-sent after a reconnect.
    if (outcome.status === 'sent') this.calls.delete(call.frame.call_id);
  }

  private failAllCalls(text: string): void {
    for (const call of this.calls.values()) this.settle(call, { status: 'unreachable', text });
    this.calls.clear();
  }

  private unreachableText(): string {
    const why = this.stopReason ? STOP_TEXT[this.stopReason] : 'Marcel is shutting down.';
    return `Marcel is unreachable. ${why} Carry on without it.`;
  }
}
