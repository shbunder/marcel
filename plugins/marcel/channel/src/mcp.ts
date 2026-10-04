// A tiny MCP stdio server: newline-delimited JSON-RPC, just what a Claude Code channel needs. No SDK.

export interface ToolResult {
  content: { type: 'text'; text: string }[];
  isError?: boolean;
}

export interface McpHandlers {
  tools: unknown[];
  instructions: string;
  callTool: (name: string, args: Record<string, unknown>) => Promise<ToolResult>;
  onInitialized: () => void;
  onNotification: (method: string, params: Record<string, unknown>) => void;
  log: (line: string) => void;
}

interface Rpc {
  jsonrpc?: string;
  id?: string | number | null;
  method?: string;
  params?: Record<string, unknown>;
}

const DEFAULT_PROTOCOL = '2024-11-05';

export class McpServer {
  initialized = false;

  constructor(
    private readonly write: (line: string) => void,
    private readonly h: McpHandlers,
    private readonly serverInfo: { name: string; version: string },
  ) {}

  notify(method: string, params: Record<string, unknown>): void {
    this.write(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }

  /** Handle one line from Claude Code. A line that breaks never stops the server. */
  async handleLine(line: string): Promise<void> {
    if (!line.trim()) return;
    let msg: Rpc;
    try {
      msg = JSON.parse(line) as Rpc;
    } catch {
      this.respondError(null, -32700, 'Parse error');
      return;
    }
    try {
      await this.dispatch(msg);
    } catch (e) {
      this.h.log(`MCP handler failed on ${String(msg.method)}: ${String(e)}`);
      if (msg.id !== undefined && msg.id !== null) this.respondError(msg.id, -32603, 'Internal error');
    }
  }

  private async dispatch(msg: Rpc): Promise<void> {
    const { id, method } = msg;
    const params = msg.params ?? {};
    const isRequest = id !== undefined && id !== null;
    switch (method) {
      case 'initialize':
        return this.respond(id, {
          protocolVersion: typeof params.protocolVersion === 'string' ? params.protocolVersion : DEFAULT_PROTOCOL,
          capabilities: { tools: {}, experimental: { 'claude/channel': {}, 'claude/channel/permission': {} } },
          serverInfo: this.serverInfo,
          instructions: this.h.instructions,
        });
      case 'notifications/initialized':
        this.initialized = true;
        this.h.onInitialized();
        return;
      case 'tools/list':
        return this.respond(id, { tools: this.h.tools });
      case 'tools/call': {
        const name = typeof params.name === 'string' ? params.name : '';
        const args = (params.arguments ?? {}) as Record<string, unknown>;
        return this.respond(id, await this.h.callTool(name, args));
      }
      case 'ping':
        return this.respond(id, {});
      default:
        if (isRequest) return this.respondError(id, -32601, `Method not found: ${String(method)}`);
        if (method) this.h.onNotification(method, params);
    }
  }

  private respond(id: Rpc['id'], result: unknown): void {
    this.write(JSON.stringify({ jsonrpc: '2.0', id, result }));
  }

  private respondError(id: Rpc['id'], code: number, message: string): void {
    this.write(JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } }));
  }
}
