import { createInterface } from 'node:readline';
import type { Readable } from 'node:stream';
import { loadConfig } from './config.js';
import type { SocketLike } from './hub.js';
import { ChannelServer } from './server.js';

export interface RunOptions {
  env: Record<string, string | undefined>;
  stdin: Readable;
  write: (line: string) => void;
  log: (line: string) => void;
  createSocket: (url: string) => SocketLike;
  readText?: (path: string) => string;
  onClose: () => void;
}

/** Start the channel server on stdin/stdout. A missing config is not fatal: the tools then say Marcel is unreachable. */
export function run(opts: RunOptions): ChannelServer {
  const result = loadConfig(opts.env, opts.readText);
  const server = new ChannelServer({
    config: result.ok ? result.config : undefined,
    configProblem: result.ok ? undefined : result.problem,
    write: opts.write,
    log: opts.log,
    createSocket: opts.createSocket,
  });
  const lines = createInterface({ input: opts.stdin });
  lines.on('line', (line) => void server.mcp.handleLine(line));
  lines.on('close', () => {
    server.stop();
    opts.onClose();
  });
  server.start();
  return server;
}
