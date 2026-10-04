import type { Config } from './config.js';
import type { Deliverable, PermissionRequest } from './frames.js';
import { REQUEST_ID } from './frames.js';
import { HubClient, type SocketLike } from './hub.js';
import { McpServer, type ToolResult } from './mcp.js';
import { TOOLS, TOOL_REPLY, TOOL_REPORT, buildReply, buildReport } from './tools.js';
import { VERSION } from './version.js';

/** The same for every role. The role comes from the hub (hello reply, event meta) and the opening prompt. */
export const INSTRUCTIONS = [
  'You are connected to Marcel, the owner’s assistant, through this channel.',
  'Messages arrive as <channel> events. Their meta says what they are: kind user_message (the owner wrote to Marcel),',
  'steer (a note for a task you are working on), or system_event (something Marcel needs you to look at).',
  'Answer the owner with the reply tool. If you are a worker, tell Marcel how the task is going with the report tool',
  '(started, progress, artifact, done, failed). Lead with the outcome, in short sentences: it is read on a phone.',
  'When a permission prompt comes up, Marcel shows it to the owner and relays the answer back to you.',
  'Your opening prompt says which role you have.',
].join(' ');

export interface ServerDeps {
  config: Config | undefined;
  /** Why there is no config, when there is none. Tools then say Marcel is unreachable. */
  configProblem?: string;
  write: (line: string) => void;
  log: (line: string) => void;
  createSocket: (url: string) => SocketLike;
  hubTiming?: Partial<ConstructorParameters<typeof HubClient>[0]>;
}

/** Ties the MCP stdio side to the hub WebSocket. */
export class ChannelServer {
  readonly mcp: McpServer;
  readonly hub: HubClient | undefined;
  private readonly held = new Map<number, Deliverable>();

  constructor(private readonly deps: ServerDeps) {
    this.mcp = new McpServer(
      deps.write,
      {
        tools: TOOLS,
        instructions: INSTRUCTIONS,
        callTool: (name, args) => this.callTool(name, args),
        onInitialized: () => this.flushHeld(),
        onNotification: (method, params) => this.onClaudeNotification(method, params),
        log: deps.log,
      },
      { name: 'marcel', version: VERSION },
    );
    if (deps.config) {
      this.hub = new HubClient({
        url: deps.config.hubUrl,
        sessionId: deps.config.sessionId,
        secret: deps.config.secret,
        pluginVersion: deps.config.pluginVersion,
        createSocket: deps.createSocket,
        onDeliver: (frame) => this.deliver(frame),
        log: deps.log,
        ...deps.hubTiming,
      });
    } else {
      deps.log(`Not connecting to the hub. ${deps.configProblem ?? 'No configuration.'}`);
    }
  }

  start(): void {
    this.hub?.start();
  }

  stop(): void {
    this.hub?.stop();
  }

  // --- hub → Claude Code ------------------------------------------------------------------------------------

  private deliver(frame: Deliverable): void {
    if (!this.mcp.initialized) {
      // Claude Code has not finished its handshake: hold the frame, unacked, so nothing is lost.
      this.held.set(frame.seq, frame);
      return;
    }
    this.emit(frame);
  }

  private flushHeld(): void {
    const frames = [...this.held.values()].sort((a, b) => a.seq - b.seq);
    this.held.clear();
    for (const frame of frames) this.emit(frame);
  }

  private emit(frame: Deliverable): void {
    switch (frame.type) {
      case 'user_message':
        this.mcp.notify('notifications/claude/channel', {
          content: frame.text,
          meta: { kind: 'user_message', message_id: frame.message_id, conversation_id: frame.conversation_id },
        });
        break;
      case 'steer':
        this.mcp.notify('notifications/claude/channel', {
          content: frame.text,
          meta: { kind: 'steer', task_id: frame.task_id, note_id: frame.note_id, author: frame.author },
        });
        break;
      case 'system_event': {
        const meta: Record<string, string> = { kind: 'system_event', event: frame.kind };
        if (frame.task_id) meta.task_id = frame.task_id;
        const data = frame.data ? `\n\n\`\`\`json\n${JSON.stringify(frame.data, null, 2)}\n\`\`\`` : '';
        this.mcp.notify('notifications/claude/channel', { content: frame.text + data, meta });
        break;
      }
      case 'permission_decision':
        this.mcp.notify('notifications/claude/channel/permission', { request_id: frame.request_id, behavior: frame.behavior });
        this.hub?.forgetPermission(frame.request_id);
        break;
    }
    this.hub?.markDelivered(frame.seq);
  }

  // --- Claude Code → hub ------------------------------------------------------------------------------------

  private onClaudeNotification(method: string, params: Record<string, unknown>): void {
    if (method !== 'notifications/claude/channel/permission_request') return;
    const id = params.request_id;
    if (typeof id !== 'string' || !REQUEST_ID.test(id)) {
      this.deps.log(`Dropped a permission request with a bad id: ${JSON.stringify(id)}.`);
      return;
    }
    const frame: PermissionRequest = {
      type: 'permission_request',
      request_id: id,
      tool_name: typeof params.tool_name === 'string' ? params.tool_name : 'unknown',
      description: typeof params.description === 'string' ? params.description : '',
    };
    if (typeof params.input_preview === 'string') frame.input_preview = params.input_preview;
    if (!this.hub) {
      this.deps.log('A permission request came in, but Marcel is not connected. Answer it in the terminal.');
      return;
    }
    this.hub.sendPermissionRequest(frame);
  }

  private async callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
    if (name !== TOOL_REPLY && name !== TOOL_REPORT) return fail(`Unknown tool: ${name}.`);
    const built = name === TOOL_REPLY ? buildReply(args) : buildReport(args);
    if (!built.ok) return fail(built.error);
    if (!this.hub) {
      return fail(`Marcel is unreachable. ${this.deps.configProblem ?? 'It is not configured.'} Carry on without it.`);
    }
    const outcome = await this.hub.sendCall(built.frame);
    return outcome.status === 'unreachable' ? fail(outcome.text) : { content: [{ type: 'text', text: outcome.text }] };
  }
}

const fail = (text: string): ToolResult => ({ content: [{ type: 'text', text }], isError: true });
