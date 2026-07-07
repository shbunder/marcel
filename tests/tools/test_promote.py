"""Tests for promote-to-extension (F3)."""

from __future__ import annotations

import subprocess

import pytest

from marcel_core.plugin.extension import ExtensionRegistry, load_extensions
from marcel_core.tools import promote as promote_mod
from marcel_core.tools.promote import (
    PromotionError,
    promote_tool_extension,
    render_tool_extension,
)


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch):
    """Scrub any ambient GIT_* so the tmp-repo fixtures target their own dirs.

    When the suite runs as the pre-commit ``make check``, git exports GIT_DIR /
    GIT_WORK_TREE / GIT_INDEX_FILE, which override ``git -C`` and would hijack
    these fixtures' ``git init``. The leak-regression test re-introduces a hook
    env explicitly, in its own body, to exercise the production scrub.
    """
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)


def _init_git_zoo(tmp_path):
    zoo = tmp_path / 'zoo'
    zoo.mkdir()
    subprocess.run(['git', '-C', str(zoo), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(zoo), 'config', 'user.email', 't@t'], check=True)
    subprocess.run(['git', '-C', str(zoo), 'config', 'user.name', 'test'], check=True)
    return zoo


# --- rendering -------------------------------------------------------------


def test_render_produces_valid_register_module():
    module = render_tool_extension('demo.greet', 'return "hi"', 'A demo greeter')
    # Compiles and contains the register entrypoint + the tool.
    compile(module, '<test>', 'exec')
    assert 'def register(marcel):' in module
    assert '@marcel.tool("demo.greet")' in module
    assert 'async def greet(params: dict, user_slug: str)' in module
    assert 'A demo greeter' in module


def test_render_indents_multiline_body():
    module = render_tool_extension('demo.calc', 'x = 1 + 1\nreturn str(x)')
    compile(module, '<test>', 'exec')
    assert '        x = 1 + 1' in module
    assert '        return str(x)' in module


def test_render_escapes_triple_quote_in_description():
    module = render_tool_extension('demo.x', 'return ""', 'evil """ desc')
    compile(module, '<test>', 'exec')  # must not break the module docstring


# --- promotion -------------------------------------------------------------


def test_promote_writes_commits_no_deploy(tmp_path):
    zoo = _init_git_zoo(tmp_path)
    path = promote_tool_extension(
        tool_id='greeter.hello',
        code='return "hello from promoted"',
        description='promoted greeter',
        zoo_dir=zoo,
        deploy=False,
    )
    assert path == zoo / 'extensions' / 'greeter' / '__init__.py'
    assert path.exists()
    # Committed to the zoo (recoverable).
    log = subprocess.run(['git', '-C', str(zoo), 'log', '--oneline'], capture_output=True, text=True)
    assert 'promote: greeter.hello' in log.stdout


@pytest.mark.parametrize('tool_id', ['nofamily', 'Bad.Id', 'a.b.c', 'UPPER.case', ''])
def test_promote_rejects_bad_tool_id(tmp_path, tool_id):
    zoo = _init_git_zoo(tmp_path)
    with pytest.raises(PromotionError):
        promote_tool_extension(tool_id=tool_id, code='return "x"', zoo_dir=zoo, deploy=False)


def test_promote_rejects_syntax_error(tmp_path):
    zoo = _init_git_zoo(tmp_path)
    with pytest.raises(PromotionError, match='does not compile'):
        promote_tool_extension(tool_id='demo.bad', code='return (unclosed', zoo_dir=zoo, deploy=False)


def test_promote_requires_zoo(monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(type(settings), 'zoo_dir', property(lambda self: None))
    with pytest.raises(PromotionError, match='MARCEL_ZOO_DIR'):
        promote_tool_extension(tool_id='demo.x', code='return "x"', zoo_dir=None, deploy=False)


def test_promote_deploy_requests_restart_via_flag(tmp_path, monkeypatch):
    zoo = _init_git_zoo(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(promote_mod, '_kernel_head_sha', lambda: 'deadbeef')
    import marcel_core.watchdog.flags as flags

    monkeypatch.setattr(flags, 'request_restart', lambda sha: calls.append(sha))

    promote_tool_extension(tool_id='deploy.me', code='return "ok"', zoo_dir=zoo, deploy=True)
    assert calls == ['deadbeef']  # the one legal restart path, with the kernel SHA


def test_promoted_extension_is_loadable(tmp_path):
    """The end goal: a promoted script loads as a real register(marcel) tool."""
    zoo = _init_git_zoo(tmp_path)
    promote_tool_extension(
        tool_id='proventool.run',
        code='return f"ran for {user_slug}"',
        zoo_dir=zoo,
        deploy=False,
    )

    reg = ExtensionRegistry()
    loaded = load_extensions(zoo, reg)
    assert 'proventool' in loaded

    from marcel_core.toolkit import get_handler

    handler = get_handler('proventool.run')
    assert handler is not None


async def test_promoted_handler_runs(tmp_path):
    zoo = _init_git_zoo(tmp_path)
    promote_tool_extension(tool_id='runnable.go', code='return "promoted-output"', zoo_dir=zoo, deploy=False)
    load_extensions(zoo, ExtensionRegistry())
    from marcel_core.toolkit import get_handler

    result = await get_handler('runnable.go')({}, 'alice')
    assert result == 'promoted-output'


def test_promote_does_not_leak_under_hook_env(tmp_path, monkeypatch):
    """Regression: promote's git ops must ignore an ambient GIT_* hook env.

    A parent git hook (notably the pre-commit ``make check``) sets GIT_DIR /
    GIT_WORK_TREE / GIT_INDEX_FILE, and those override ``git -C``. Before the
    fix, promote_tool_extension staged+committed the extension into the hook's
    in-flight repo instead of the target zoo — leaking test fixtures into the
    real index and (worse) committing to the real repo on the self-mod path.
    """
    # Scrub first so the fixtures' own git init/add/commit don't hit the bug.
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)

    outer = tmp_path / 'outer'
    outer.mkdir()
    subprocess.run(['git', '-C', str(outer), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(outer), 'config', 'user.email', 't@e'], check=True)
    subprocess.run(['git', '-C', str(outer), 'config', 'user.name', 'T'], check=True)
    subprocess.run(['git', '-C', str(outer), 'commit', '-q', '--allow-empty', '-m', 'seed'], check=True)
    zoo = _init_git_zoo(tmp_path)

    # Simulate the pre-commit hook env pointing at the outer repo.
    monkeypatch.setenv('GIT_DIR', str(outer / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(outer))
    monkeypatch.setenv('GIT_INDEX_FILE', str(outer / '.git' / 'index'))

    promote_tool_extension(tool_id='leak.check', code='return "x"', zoo_dir=zoo, deploy=False)

    # Restore a clean env before verifying, so these greps target what we name.
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)

    staged = subprocess.check_output(['git', '-C', str(outer), 'diff', '--cached', '--name-only']).decode()
    assert 'extensions' not in staged, f'leaked into outer index: {staged!r}'
    # ...and the extension really landed + committed in the zoo.
    assert (zoo / 'extensions' / 'leak' / '__init__.py').exists()
    log = subprocess.run(['git', '-C', str(zoo), 'log', '--oneline'], capture_output=True, text=True)
    assert 'promote: leak.check' in log.stdout
