import { describe, expect, it } from 'vitest';
import { DEFAULT_HUB_URL, loadConfig } from '../src/config.js';
import { SECRET, SESSION_ID } from './harness.js';

const env = { CLAUDE_CODE_SESSION_ID: SESSION_ID, CLAUDE_PLUGIN_DATA: '/data/marcel' };

describe('loadConfig', () => {
  it('reads the session id from the environment and the secret from $CLAUDE_PLUGIN_DATA/hub-secret', () => {
    const paths: string[] = [];
    const r = loadConfig(env, (p) => (paths.push(p), `${SECRET}\n`));
    expect(paths).toEqual(['/data/marcel/hub-secret']);
    expect(r).toEqual({ ok: true, config: { hubUrl: DEFAULT_HUB_URL, sessionId: SESSION_ID, secret: SECRET, pluginVersion: '0.1.0' } });
  });

  it('connects to the hub’s port on the NUC, outside /api', () => {
    expect(DEFAULT_HUB_URL).toBe('ws://127.0.0.1:7420/channel');
  });

  it('lets a development environment point at another hub', () => {
    const r = loadConfig({ ...env, MARCEL_HUB_URL: 'ws://127.0.0.1:9999/channel' }, () => SECRET);
    expect(r.ok && r.config.hubUrl).toBe('ws://127.0.0.1:9999/channel');
  });

  it('says what is wrong, in plain words', () => {
    const read = () => SECRET;
    expect(loadConfig({ ...env, CLAUDE_CODE_SESSION_ID: undefined }, read)).toMatchObject({ ok: false, problem: expect.stringContaining('CLAUDE_CODE_SESSION_ID') });
    expect(loadConfig({ ...env, CLAUDE_CODE_SESSION_ID: 'not-a-uuid' }, read).ok).toBe(false);
    expect(loadConfig({ ...env, CLAUDE_PLUGIN_DATA: undefined }, read)).toMatchObject({ ok: false, problem: expect.stringContaining('CLAUDE_PLUGIN_DATA') });
    expect(loadConfig(env, () => { throw new Error('ENOENT'); })).toMatchObject({ ok: false, problem: expect.stringContaining('/data/marcel/hub-secret') });
    expect(loadConfig(env, () => '  \n')).toMatchObject({ ok: false, problem: expect.stringContaining('empty') });
  });

  it('reads the real file by default', async () => {
    const { mkdtempSync, writeFileSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const dir = mkdtempSync(join(tmpdir(), 'marcel-data-'));
    writeFileSync(join(dir, 'hub-secret'), `${SECRET}\n`);
    const r = loadConfig({ ...env, CLAUDE_PLUGIN_DATA: dir });
    expect(r.ok && r.config.secret).toBe(SECRET);
  });
});
