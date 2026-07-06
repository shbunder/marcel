"""Tests for the bubblewrap workspace-write sandbox.

The argv-construction, availability-probe, and fallback logic are tested
directly. The real confinement test is skipped where a working sandbox
(bubblewrap + unprivileged user namespaces) is unavailable — it runs in an
environment that has one (e.g. the prod container once userns is enabled).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marcel_core.harness import sandbox
from marcel_core.harness.sandbox import build_bwrap_argv, run_sandboxed, sandbox_available


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    sandbox_available.cache_clear()
    yield
    sandbox_available.cache_clear()


# --- availability probe ----------------------------------------------------


def test_available_false_when_bwrap_absent(monkeypatch):
    monkeypatch.setattr(sandbox, 'bwrap_path', lambda: None)
    assert sandbox_available() is False


def test_available_false_when_probe_fails(monkeypatch):
    monkeypatch.setattr(sandbox, 'bwrap_path', lambda: '/usr/bin/bwrap')

    def fake_run(*_a, **_kw):
        return subprocess.CompletedProcess([], returncode=1, stdout=b'', stderr=b'uid map: Permission denied')

    monkeypatch.setattr(sandbox.subprocess, 'run', fake_run)
    assert sandbox_available() is False


def test_available_true_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(sandbox, 'bwrap_path', lambda: '/usr/bin/bwrap')

    def fake_run(*_a, **_kw):
        return subprocess.CompletedProcess([], returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(sandbox.subprocess, 'run', fake_run)
    assert sandbox_available() is True


def test_available_false_on_oserror(monkeypatch):
    monkeypatch.setattr(sandbox, 'bwrap_path', lambda: '/usr/bin/bwrap')

    def boom(*_a, **_kw):
        raise OSError('nope')

    monkeypatch.setattr(sandbox.subprocess, 'run', boom)
    assert sandbox_available() is False


# --- argv construction -----------------------------------------------------


def _argv(monkeypatch, workspace, data_dir, *, allow_network):
    monkeypatch.setattr(sandbox, 'bwrap_path', lambda: '/usr/bin/bwrap')
    return build_bwrap_argv(
        workspace=workspace,
        cwd=workspace,
        data_dir=data_dir,
        allow_network=allow_network,
    )


def test_argv_core_structure(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    ws.mkdir()
    argv = _argv(monkeypatch, ws, tmp_path / 'data', allow_network=True)
    joined = ' '.join(argv)
    assert argv[0] == '/usr/bin/bwrap'
    assert '--unshare-user' in argv
    assert '--die-with-parent' in argv
    # broad read-only root, then a single writable workspace bind.
    assert '--ro-bind / /' in joined
    assert f'--bind {ws} {ws}' in joined
    assert f'--chdir {ws}' in joined


def test_argv_network_toggle(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    ws.mkdir()
    assert '--unshare-net' in _argv(monkeypatch, ws, tmp_path / 'd', allow_network=False)
    assert '--unshare-net' not in _argv(monkeypatch, ws, tmp_path / 'd', allow_network=True)


def test_argv_self_mod_paths_readonly(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    (ws / '.git').mkdir(parents=True)
    (ws / 'CLAUDE.md').write_text('x')
    (ws / 'src' / 'marcel_core' / 'auth').mkdir(parents=True)
    (ws / 'src' / 'marcel_core' / 'config.py').write_text('x')
    (ws / '.env.local').write_text('SECRET=1')
    data = tmp_path / 'marcel-data'
    data.mkdir()

    joined = ' '.join(_argv(monkeypatch, ws, data, allow_network=True))
    # Each self-mod path is re-bound read-only inside the writable workspace.
    assert f'--ro-bind {ws / ".git"} {ws / ".git"}' in joined
    assert f'--ro-bind {data} {data}' in joined  # ~/.marcel + restart flag
    assert f'--ro-bind {ws / "CLAUDE.md"} {ws / "CLAUDE.md"}' in joined
    assert f'--ro-bind {ws / "src" / "marcel_core" / "config.py"}' in joined
    assert f'--ro-bind {ws / ".env.local"} {ws / ".env.local"}' in joined


def test_argv_omits_absent_self_mod_paths(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    ws.mkdir()  # no .git / CLAUDE.md etc.
    joined = ' '.join(_argv(monkeypatch, ws, tmp_path / 'd', allow_network=True))
    assert 'CLAUDE.md' not in joined
    assert '.git' not in joined


# --- real confinement (only where a working sandbox exists) ----------------


@pytest.mark.skipif(not sandbox_available(), reason='needs bubblewrap + unprivileged user namespaces')
async def test_confinement_denies_write_outside_workspace(tmp_path):
    ws = tmp_path / 'ws'
    ws.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()

    rc, _out, _err = await run_sandboxed(
        f'echo pwned > {outside}/escape.txt',
        workspace=ws,
        cwd=ws,
        data_dir=tmp_path / 'data',
        timeout=10,
    )
    assert rc != 0
    assert not (outside / 'escape.txt').exists()

    # A write *inside* the workspace succeeds.
    rc2, _o, _e = await run_sandboxed(
        'echo ok > allowed.txt',
        workspace=ws,
        cwd=ws,
        data_dir=tmp_path / 'data',
        timeout=10,
    )
    assert rc2 == 0
    assert (ws / 'allowed.txt').exists()


@pytest.mark.skipif(not sandbox_available(), reason='needs bubblewrap + unprivileged user namespaces')
async def test_confinement_self_mod_readonly(tmp_path):
    ws = tmp_path / 'ws'
    (ws / '.git').mkdir(parents=True)
    rc, _o, _e = await run_sandboxed(
        'echo x > .git/hook',
        workspace=ws,
        cwd=ws,
        data_dir=tmp_path / 'data',
        timeout=10,
    )
    assert rc != 0
    assert not (ws / '.git' / 'hook').exists()


# --- bash routing (sandbox when available, else fallback) ------------------


async def test_bash_falls_back_when_sandbox_unavailable(monkeypatch):
    from marcel_core.tools import core

    monkeypatch.setattr(sandbox, 'sandbox_available', lambda: False)
    rc, out, _err = await core._run_bash('echo hello-fallback', None, 10)
    assert rc == 0
    assert b'hello-fallback' in out


async def test_bash_uses_sandbox_when_available(monkeypatch):
    from marcel_core.tools import core

    captured: dict = {}

    async def fake_run_sandboxed(command, **kwargs):
        captured['command'] = command
        captured.update(kwargs)
        return 0, b'sandboxed-output', b''

    monkeypatch.setattr(sandbox, 'sandbox_available', lambda: True)
    monkeypatch.setattr(sandbox, 'run_sandboxed', fake_run_sandboxed)

    rc, out, _err = await core._run_bash('echo hi', '/tmp', 10)
    assert out == b'sandboxed-output'
    assert captured['command'] == 'echo hi'
    assert captured['workspace'] == Path('/tmp')


async def test_bash_disabled_config_skips_sandbox(monkeypatch):
    from marcel_core.config import settings
    from marcel_core.tools import core

    def _must_not_probe():
        raise AssertionError('sandbox_available must not be called when disabled')

    # Even if a sandbox were available, disabling the config runs unsandboxed.
    monkeypatch.setattr(settings, 'marcel_sandbox_enabled', False)
    monkeypatch.setattr(sandbox, 'sandbox_available', _must_not_probe)
    rc, out, _err = await core._run_bash('echo direct', None, 10)
    assert rc == 0
    assert b'direct' in out
