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
// folder with the plugin (MARCEL_LIVE_CWD, default ~/marcel/workspaces/scratch), and the hub secret
// (MARCEL_LIVE_SECRET, default ~/marcel/data/hub-secret). Every session it starts runs `haiku` and is
// stopped at the end. What the owner sees: a device paired and removed, an adopted quest and a short
// worker quest (archived at the end), one Write approval card, and the brain replaced once (the
// respawn case stops it on purpose; the hub starts a new one with its context).
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

const SECRET_FILE = process.env.MARCEL_LIVE_SECRET ?? join(homedir(), 'marcel', 'data', 'hub-secret');

/** The brain session the runner lists now (role `brain`). */
async function brainId(): Promise<string | undefined> {
  const { json } = await runner('GET', '/sessions?all=false');
  const rows = (json as unknown as any[]) ?? [];
  return rows.find((r) => r.role === 'brain' && r.status)?.id;
}

/** The brain API, as the brain's own tools call it (hub secret and the brain's session id). */
async function brainApi(method: string, path: string, body?: Json): Promise<{ status: number; json: any }> {
  const res = await fetch(`${HUB}/brain${path}`, {
    method,
    headers: {
      'x-marcel-secret': readFileSync(SECRET_FILE, 'utf8').trim(),
      'x-marcel-session': (await brainId()) ?? '',
      ...(body ? { 'content-type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : {} };
}

let deviceToken = '';
let deviceId = '';
const started: string[] = []; // sessions this test started
const tasks: string[] = []; // tasks this test made, archived at the end

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
  expect(paired.status, 'POST /api/pair').toBe(201);
  const device = (await paired.json()) as { token: string; device_id: string };
  deviceToken = device.token;
  deviceId = device.device_id;
}, 60_000);

afterAll(async () => {
  const problems: string[] = [];
  // An approval this test left open is denied, never left for the owner's phone.
  const open = await api('GET', '/api/approvals').catch(() => ({ json: [] as any[] }));
  for (const a of (Array.isArray(open.json) ? open.json : []) as any[]) {
    if (tasks.includes(a.task_id) && a.state === 'open') {
      await api('POST', `/api/approvals/${a.id}`, { decision: 'deny', note: 'The live test ended.' });
    }
  }
  for (const sid of started) {
    const stop = await runner('POST', `/sessions/${sid}/stop`).catch((e: Error) => ({ status: 0, json: { message: e.message } }));
    if (stop.status !== 200) problems.push(`session ${sid} was not stopped: ${stop.status} ${JSON.stringify(stop.json)}`);
  }
  for (const t of tasks) await api('POST', `/api/tasks/${t}/archive`).catch(() => undefined);
  if (deviceId) await api('DELETE', `/api/devices/${deviceId}`).catch(() => undefined);
  rmSync(join(CWD, `live-approval-${STAMP}.txt`), { force: true });
  if (problems.length) throw new Error(problems.join('\n'));
}, 180_000);

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
    sessionId = res.json.session_id as string;
    started.push(sessionId);
    // The runner answers only once the debug log says `Channel notifications registered`; the
    // only channel is the marcel server, which plugin.json declares inline (S-05.1 review).
    expect(res.json.channel_registered).toBe(true);
  }, 240_000);

  it('is adopted, and a message sent through the hub reaches it and its answer comes back', async () => {
    const adopt = await fetch(`${HUB}/api/session/adopt`, {
      method: 'POST',
      headers: {
        // The file holds the adopt code `mra_…@<hub>` (S-22.2): the bearer is the token before the `@`.
        authorization: `Bearer ${readFileSync(join(homedir(), '.marcel', 'adopt-token'), 'utf8').trim().split('@')[0]}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ session_id: sessionId, kind: 'local', title: `Channel live ${STAMP}`, cwd: CWD, machine: hostname() }),
    });
    const adopted = (await adopt.json()) as { task_id: string; tracked: string };
    expect(adopt.status, JSON.stringify(adopted)).toBe(201);
    expect(adopted.tracked).toBe('live');
    taskId = adopted.task_id;
    tasks.push(taskId);

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

  it('delivers a message sent while a new worker is still starting, once its channel says hello', async () => {
    // The hub stores a steer for a task whose session has not said hello yet and sends it at the
    // hello; the channel server holds frames until Claude Code is ready for them (the settle delay
    // after notifications/initialized, SP1). Started through the brain API, as tasks_spawn does.
    const word = `EARLY${STAMP}`;
    const made = await brainApi('POST', '/tasks', {
      title: `Channel live early message ${STAMP}`,
      location: 'nuc',
      model: 'haiku',
      prompt: 'Wait for a message on the channel and do what it says. Do not report until you have it.',
    });
    expect(made.status, JSON.stringify(made.json)).toBe(201);
    const early = made.json.id as string;
    tasks.push(early);
    const sent = await api('POST', `/api/tasks/${early}/messages`, { text: `Please answer with exactly the text "${word}".` });
    expect(sent.status).toBe(202);
    await waitFor('the early message, answered', () => answered(early, word));
    // The hub names a worker's session after its task; stop it with the rest at the end.
    const { json: rows } = await runner('GET', '/sessions?all=false');
    const worker = ((rows as unknown as any[]) ?? []).find((r) => String(r.name).startsWith(early));
    if (worker) started.push(worker.id);
  }, 300_000);

  it('gets a message sent while the brain was down to the brain that replaces it', async () => {
    // A brain that ends is replaced at once (the hub's keeper); a message sent meanwhile waits in
    // the hub and reaches the new brain at its hello.
    const old = await brainId();
    expect(old, 'a running brain').toBeTruthy();
    const { json: me } = await api('GET', '/api/me');
    const conversation = me.agents[0].main_conversation_id as string;
    expect((await runner('POST', `/sessions/${old}/stop`)).status).toBe(200);
    const word = `RESPAWN${STAMP}`;
    const sent = await api('POST', `/api/conversations/${conversation}/messages`, {
      text: `A live test: please answer with exactly the text "${word}" and nothing else.`,
    });
    expect(sent.status).toBe(201);
    const reply = await waitFor('the new brain\'s answer', async () => {
      const { json } = await api('GET', `/api/conversations/${conversation}/messages`);
      const items: any[] = json.items ?? json;
      return items.find((m) => m.role === 'agent' && String(m.text).includes(word));
    }, 300);
    expect(reply).toBeTruthy();
    expect(await brainId()).not.toBe(old);
  }, 420_000);

  // Not here: a prompt answered in the terminal (`claude attach`) and not shown again as a card
  // after a reconnect. It needs a person at the terminal (S-05.1 review; follow-up for the lead).
});
