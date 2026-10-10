import { run } from './run.js';
import type { SocketLike } from './hub.js';

// stdout belongs to MCP; everything a person might read goes to stderr, which Claude Code keeps in its debug log.
run({
  env: process.env,
  stdin: process.stdin,
  write: (line) => void process.stdout.write(`${line}\n`),
  log: (line) => void process.stderr.write(`[marcel-channel] ${line}\n`),
  createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
  onClose: () => process.exit(0),
});
