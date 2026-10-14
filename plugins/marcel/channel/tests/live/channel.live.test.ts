// S-05.2: the channel with a real `claude --bg` session on the NUC, against the deployed hub and runner.
//
// Run by hand on the NUC, after `make -C deploy install` (never part of the gate):
//
//     cd plugins/marcel/channel && npx vitest run --config tests/live/vitest.config.ts
//
// It needs: the hub on MARCEL_LIVE_HUB (default http://127.0.0.1:7420), the runner's socket at
// MARCEL_LIVE_RUNNER (default ~/marcel/run/runner.sock), the owner's adopt token in
// ~/.marcel/adopt-token (`marcel-hub adopt-token`), Docker (it pairs a device with
// `docker exec marcel-hub marcel-hub pair` and removes it at the end), and the trusted scratch
// folder with the plugin (MARCEL_LIVE_CWD, default ~/marcel/workspaces/scratch). Every session it
// starts runs `haiku` and is stopped at the end.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
import { homedir, hostname } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const HUB = process.env.MARCEL_LIVE_HUB ?? 'http://127.0.0.1:7420';
const RUNNER = process.env.MARCEL_LIVE_RUNNER ?? join(homedir(), 'marcel', 'run', 'runner.sock');
const CWD = process.env.MARCEL_LIVE_CWD ?? join(homedir(), 'marcel', 'workspaces', 'scratch');
const STAMP = new Date().toISOString().slice(11, 19).replace(/:/g, '');

type Json = Record<string, unknown>;

/** The runner's HTTP API over its unix socket. */
function runner(method: string, path: string, body?: Json): Promise<{ status: number; json: Json }> {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const req = httpRequest(
      {
        socketPath: RUNNER,
        path,
        method,
        headers: data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {},
        timeout: 180_000,
      },
      (res) => {
        let text = '';
        res.on('data', (c: Buffer) => (text += c.toString()));
        res.on('end', () => resolve({ status: res.statusCode ?? 0, json: text ? (JSON.parse(text) as Json) : {} }));
      },
    );
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

let deviceToken = '';
let deviceId = '';
const started: string[] = [];

async function api(method: string, path: string, body?: Json, token = deviceToken): Promise<{ status: number; json: any }> {
  const res = await fetch(`${HUB}${path}`, {
    method,
    headers: { authorization: `Bearer ${token}`, ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : {} };
}

async function waitFor<T>(what: string, check: () => Promise<T | undefined>, seconds = 180): Promise<T> {
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    const found = await check();
    if (found !== undefined) return found;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`${what}: not within ${seconds} s`);
}

/** The task's events of one type, oldest first (`GET /api/tasks/{id}/events`, paged by `after`). */
async function events(taskId: string, type: string): Promise<any[]> {
  const all: any[] = [];
  let after = 0;
  for (;;) {
    const { json } = await api('GET', `/api/tasks/${taskId}/events?after=${after}`);
    const page: any[] = json.items ?? [];
    all.push(...page);
    if (!json.has_more || page.length === 0) break;
    after = page[page.length - 1].seq;
  }
  return all.filter((e) => e.type === type);
}

/** The session's own answer holding `word`: an assistant `text` event in the task's transcript,
 * which the hub stores from what the runner tails (an adopted session answers in its transcript;
 * it has no worker protocol telling it to call `report`). */
async function answered(taskId: string, word: string): Promise<any | undefined> {
  const said = await events(taskId, 'text');
  return said.find((e) => e.data?.role === 'assistant' && String(e.data?.text ?? '').includes(word));
}

beforeAll(async () => {
  const shown = execFileSync('docker', ['exec', 'marcel-hub', 'marcel-hub', 'pair', '--hub', `${HUB}/api`], {
    encoding: 'utf8',
  });
  const code = /type the code ([A-Za-z0-9]+)\./.exec(shown)?.[1];
  expect(code, 'marcel-hub pair printed no code').toBeTruthy();
  const paired = await fetch(`${HUB}/api/pair`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code, device_name: `S-05.2 live test ${STAMP}` }),
  });
  const device = (await paired.json()) as { token: string; device_id: string };
  deviceToken = device.token;
  deviceId = device.device_id;
}, 60_000);

afterAll(async () => {
  for (const sid of started) await runner('POST', `/sessions/${sid}/stop`).catch(() => undefined);
  if (deviceId) await api('DELETE', `/api/devices/${deviceId}`).catch(() => undefined);
  rmSync(join(CWD, `live-approval-${STAMP}.txt`), { force: true });
}, 120_000);

describe('the marcel channel with a real background session', () => {
  let sessionId = '';
  let taskId = '';

  it('starts a manual-mode session whose inline plugin registers its channel', async () => {
    const res = await runner('POST', '/sessions', {
      role: 'adopted',
      name: `live-channel-${STAMP}`,
      model: 'haiku',
      permission_mode: 'manual',
      channel: true,
      cwd: CWD,
      prompt: 'Answer READY, then wait for messages.',
    });
    expect(res.status, JSON.stringify(res.json)).toBe(201);
    // The runner answers only once the debug log says `Channel notifications registered` for
    // plugin:marcel:marcel, the MCP server plugin.json declares inline (S-05.1 review).
    expect(res.json.channel_registered).toBe(true);
    sessionId = res.json.session_id as string;
    started.push(sessionId);
  }, 240_000);

  it('is adopted, and a message sent through the hub reaches it and its answer comes back', async () => {
    const adopt = await fetch(`${HUB}/api/session/adopt`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${readFileSync(join(homedir(), '.marcel', 'adopt-token'), 'utf8').trim()}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ session_id: sessionId, kind: 'local', title: `Channel live ${STAMP}`, cwd: CWD, machine: hostname() }),
    });
    const adopted = (await adopt.json()) as { task_id: string; tracked: string };
    expect(adopt.status, JSON.stringify(adopted)).toBe(201);
    expect(adopted.tracked).toBe('live');
    taskId = adopted.task_id;

    const word = `PINEAPPLE${STAMP}`;
    const sent = await api('POST', `/api/tasks/${taskId}/messages`, {
      text: `Please answer with exactly the text "${word}". Do nothing else.`,
    });
    expect(sent.status).toBe(202);
    await waitFor('the answer', () => answered(taskId, word));
  }, 300_000);

  it('raises an approval card for a write, and the write happens once it is approved through the API', async () => {
    const file = `live-approval-${STAMP}.txt`;
    await api('POST', `/api/tasks/${taskId}/messages`, {
      // The absolute path in the workspace: a background session may write its own job folder
      // (~/.claude/jobs/<id>/tmp) without asking, and "your folder" was once read that way.
      text: `Please use the Write tool to create the file ${join(CWD, file)} containing the word ok, then answer with the text "WRITTEN${STAMP}".`,
    });
    const card = await waitFor('the approval card', async () => {
      const { json } = await api('GET', '/api/approvals');
      const items: any[] = Array.isArray(json) ? json : (json.items ?? []);
      return items.find((a) => a.task_id === taskId && a.tool === 'Write' && a.state === 'open');
    });
    expect(String(card.detail)).toContain(file);
    expect(existsSync(join(CWD, file)), 'nothing is written before the approval').toBe(false);

    const answer = await api('POST', `/api/approvals/${card.id}`, { decision: 'approve' });
    expect(answer.status).toBe(200);
    expect(answer.json.state).toBe('approved');
    await waitFor('the file', async () => (existsSync(join(CWD, file)) ? true : undefined));
    expect(readFileSync(join(CWD, file), 'utf8').trim()).toBe('ok');
    await waitFor('the answer after the write', () => answered(taskId, `WRITTEN${STAMP}`));
  }, 300_000);

  // Not here: a message "queued while the session was stopped". Stopping an adopted session ends its
  // task (`stopped`), and a stopped task refuses messages (409 `task_finished`), so whether one gets in
  // first is a race (it passed 6 of 7 runs). The channel's reconnect is covered by its own tests and by
  // the hub restart on the NUC (S-14.1 log).
});
