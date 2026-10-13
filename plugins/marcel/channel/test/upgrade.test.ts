// The hub answers the WebSocket upgrade with a plain HTTP error (seen live: a hub image without a WS library
// answered 404), or is not listening yet. That is the hub being down, not a rejection: Node's WebSocket fires only
// `error` for a failed handshake, and the server must still retry with backoff until the hub is back.
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { WebSocketServer } from 'ws';
import { afterEach, describe, expect, it } from 'vitest';
import { HubClient, type SocketLike } from '../src/hub.js';
import { SECRET, SESSION_ID } from './harness.js';

let server: Server | undefined;
let client: HubClient | undefined;

afterEach(async () => {
  client?.stop();
  await new Promise((r) => (server?.listening ? server.close(r) : r(undefined)));
  server = undefined;
});

/** A hub that answers the first `refusals` upgrades with `status`, then speaks the protocol. */
function hub(refusals: number, status: string): Server {
  const wss = new WebSocketServer({ noServer: true });
  const s = createServer();
  s.on('upgrade', (req, socket, head) => {
    if (refusals > 0) {
      refusals -= 1;
      socket.end(`HTTP/1.1 ${status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n`);
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      ws.once('message', () => {
        ws.send(JSON.stringify({ type: 'welcome', role: 'brain', agent_id: 'agt_marcel', conversation_id: 'cnv_main_marcel' }));
        ws.send(JSON.stringify({ type: 'user_message', seq: 1, message_id: 'msg_1', conversation_id: 'cnv_main_marcel', text: 'Hello' }));
      });
    });
  });
  return s;
}

const listen = (s: Server, port = 0) => new Promise<number>((r) => s.listen(port, '127.0.0.1', () => r((s.address() as AddressInfo).port)));

function connect(port: number) {
  const logs: string[] = [];
  const delivered: unknown[] = [];
  client = new HubClient({
    url: `ws://127.0.0.1:${port}/channel`,
    sessionId: SESSION_ID,
    secret: SECRET,
    pluginVersion: '0.1.0',
    createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
    onDeliver: (f) => delivered.push(f),
    log: (l) => logs.push(l),
    backoffStartMs: 20,
    backoffMaxMs: 100,
  });
  client.start();
  const delivery = async () => {
    for (let i = 0; i < 200 && delivered.length === 0; i++) await new Promise((r) => setTimeout(r, 25));
  };
  return { logs, delivered, delivery };
}

describe('HTTP error or no hub on the WebSocket upgrade', () => {
  it.each(['404 Not Found', '503 Service Unavailable'])('answered %s twice, then up: reconnects and delivers', async (status) => {
    server = hub(2, status);
    const c = connect(await listen(server));
    await c.delivery();
    expect(c.delivered).toHaveLength(1);
    expect(client?.state).toBe('ready');
    // two failed tries, one line (the rest are throttled), then one line when it is back
    const tries = c.logs.filter((l) => l.startsWith('Could not connect to the hub'));
    expect(tries).toEqual([expect.stringMatching(/answered with an HTTP error instead of a WebSocket\. Trying again in 20 ms\.$/)]);
    expect(c.logs.filter((l) => l.startsWith('Reconnected to the hub after'))).toHaveLength(1);
  });

  it('connection refused until the hub starts listening: reconnects and delivers', async () => {
    const probe = createServer();
    const port = await listen(probe);
    await new Promise((r) => probe.close(r));
    const c = connect(port);
    await new Promise((r) => setTimeout(r, 150));
    expect(c.logs.some((l) => l.startsWith('Could not connect to the hub'))).toBe(true);
    server = hub(0, '');
    await listen(server, port);
    await c.delivery();
    expect(c.delivered).toHaveLength(1);
    expect(client?.state).toBe('ready');
  });
});
