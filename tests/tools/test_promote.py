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
