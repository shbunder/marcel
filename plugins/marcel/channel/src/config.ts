import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { VERSION } from './version.js';

export const DEFAULT_HUB_URL = 'ws://127.0.0.1:7420/channel';
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export interface Config {
  hubUrl: string;
  sessionId: string;
  secret: string;
  pluginVersion: string;
}

export type ConfigResult = { ok: true; config: Config } | { ok: false; problem: string };

/**
 * Identity is `CLAUDE_CODE_SESSION_ID`; the secret is the file `hub-secret` in `CLAUDE_PLUGIN_DATA`.
 * Env set on the `claude --bg` command line never reaches this process (SP1, SP7), so nothing else is read.
 * `MARCEL_HUB_URL` exists for development against a hub on another port.
 */
export function loadConfig(
  env: Record<string, string | undefined>,
  readText: (path: string) => string = (p) => readFileSync(p, 'utf8'),
): ConfigResult {
  const sessionId = env.CLAUDE_CODE_SESSION_ID ?? '';
  if (!SESSION_ID.test(sessionId)) {
    return { ok: false, problem: 'CLAUDE_CODE_SESSION_ID is missing or not a session id, so Marcel cannot tell who this session is.' };
  }
  const dataDir = env.CLAUDE_PLUGIN_DATA;
  if (!dataDir) {
    return { ok: false, problem: 'CLAUDE_PLUGIN_DATA is not set, so the hub secret cannot be found.' };
  }
  const secretPath = join(dataDir, 'hub-secret');
  let secret: string;
  try {
    secret = readText(secretPath).trim();
  } catch {
    return { ok: false, problem: `The hub secret is missing: ${secretPath} could not be read. Deploy writes it.` };
  }
  if (!secret) {
    return { ok: false, problem: `The hub secret file ${secretPath} is empty.` };
  }
  return { ok: true, config: { hubUrl: env.MARCEL_HUB_URL || DEFAULT_HUB_URL, sessionId, secret, pluginVersion: VERSION } };
}
