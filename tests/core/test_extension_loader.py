"""Tests for the register(marcel) extension loader (F0.3)."""

from __future__ import annotations

from pathlib import Path

from marcel_core.plugin.extension import (
    ExtensionRegistry,
    emit_resources_discover,
    load_extensions,
)
from marcel_sdk.events import EventBus

# A full demo extension: registers a tool, a channel, an on() handler, and
# records a skill/job/agent/command. Unique names avoid collisions with the
# process-wide toolkit/channel registries and the sys.modules import cache.
_DEMO = """
def register(marcel):
    @marcel.tool("extloader.ping")
    async def ping(params, user_slug):
        return "pong"

    class _Chan:
        name = "extloader_chan"
        router = None

    marcel.channel(_Chan())

    def audit(event, ctx):
        pass

    marcel.on("tool_call", audit)
    marcel.skill("/zoo/skills/extloader")
    marcel.connector("/zoo/connectors/extloader")
    marcel.job("/zoo/jobs/extloader")
    marcel.agent("/zoo/agents/extloader.md")

    async def cmd(args, ctx):
        pass

    marcel.command("extloader", cmd)
"""


def _write_ext(zoo: Path, name: str, src: str) -> Path:
    ext = zoo / 'extensions' / name
    ext.mkdir(parents=True)
    (ext / '__init__.py').write_text(src)
    return zoo


def test_none_zoo_returns_empty():
    assert load_extensions(None, ExtensionRegistry()) == []


def test_no_extensions_dir_returns_empty(tmp_path):
    assert load_extensions(tmp_path, ExtensionRegistry()) == []


def test_loads_and_registers_all_kinds(tmp_path):
    reg = ExtensionRegistry()
    zoo = _write_ext(tmp_path, 'extloader_demo', _DEMO)

    loaded = load_extensions(zoo, reg)
    assert loaded == ['extloader_demo']

    # tool → deprecated no-op (the toolkit habitat retired, FEAT-260718-c232d9);
    # the extension still loads, its other registrations still land.

    # channel → channel registry
    from marcel_core.plugin.channels import get_channel

    assert get_channel('extloader_chan') is not None

    # on() handler → collected for bus replay
    assert [name for name, _ in reg.handlers] == ['tool_call']

    # skill/connector/job/agent/command → recorded
    assert reg.skills == ['/zoo/skills/extloader']
    # Connectors are a registerable habitat kind too (FR5, FEAT-260718-230bf8).
    assert reg.connectors == ['/zoo/connectors/extloader']
    assert reg.jobs == ['/zoo/jobs/extloader']
    assert reg.agents == ['/zoo/agents/extloader.md']
    assert 'extloader' in reg.commands


def test_extension_without_register_is_skipped(tmp_path):
    reg = ExtensionRegistry()
    zoo = _write_ext(tmp_path, 'extloader_noreg', 'X = 1  # no register()\n')
    assert load_extensions(zoo, reg) == []


def test_extension_whose_register_raises_is_skipped(tmp_path):
    reg = ExtensionRegistry()
    src = 'def register(marcel):\n    raise RuntimeError("boom")\n'
    zoo = _write_ext(tmp_path, 'extloader_boom', src)
    assert load_extensions(zoo, reg) == []


def test_broken_extension_does_not_block_good_one(tmp_path):
    reg = ExtensionRegistry()
    (tmp_path / 'extensions').mkdir()
    bad = tmp_path / 'extensions' / 'extloader_bad'
    bad.mkdir()
    (bad / '__init__.py').write_text('def register(marcel):\n    raise ValueError("x")\n')
    good = tmp_path / 'extensions' / 'extloader_good'
    good.mkdir()
    (good / '__init__.py').write_text(
        'def register(marcel):\n'
        '    @marcel.tool("extloadergood.ok")\n'
        '    async def ok(params, user_slug):\n'
        '        return "ok"\n',
    )
    loaded = load_extensions(tmp_path, reg)
    assert loaded == ['extloader_good']


def test_loads_single_file_extension(tmp_path):
    """A ``.py`` file (not a package dir) exposing register() also loads."""
    reg = ExtensionRegistry()
    (tmp_path / 'extensions').mkdir()
    (tmp_path / 'extensions' / 'extloader_file.py').write_text(
        'def register(marcel):\n'
        '    @marcel.tool("extloaderfile.ok")\n'
        '    async def ok(params, user_slug):\n'
        '        return "ok"\n',
    )
    assert load_extensions(tmp_path, reg) == ['extloader_file']


def test_non_python_files_ignored(tmp_path):
    reg = ExtensionRegistry()
    (tmp_path / 'extensions').mkdir()
    (tmp_path / 'extensions' / 'README.txt').write_text('not an extension')
    assert load_extensions(tmp_path, reg) == []


def test_underscore_entries_ignored(tmp_path):
    reg = ExtensionRegistry()
    (tmp_path / 'extensions').mkdir()
    private = tmp_path / 'extensions' / '_private'
    private.mkdir()
    (private / '__init__.py').write_text('def register(marcel):\n    raise AssertionError("should not run")\n')
    assert load_extensions(tmp_path, reg) == []


# --- ExtensionRegistry + API unit tests -----------------------------------


def test_apply_to_bus_subscribes_handlers():
    reg = ExtensionRegistry()
    calls: list[int] = []
    reg.handlers.append(('tool_call', lambda e, c: calls.append(1)))
    reg.handlers.append(('agent_end', lambda e, c: None))
    bus = EventBus()
    reg.apply_to_bus(bus)
    assert bus.handler_count('tool_call') == 1
    assert bus.handler_count('agent_end') == 1


def test_registry_clear():
    async def _cmd(args, ctx):
        return None

    reg = ExtensionRegistry()
    reg.skills.append('x')
    reg.handlers.append(('input', lambda e, c: None))
    reg.commands['c'] = _cmd
    reg.clear()
    assert not reg.skills and not reg.handlers and not reg.commands


async def test_emit_resources_discover_collects_contributions(tmp_path):
    reg = ExtensionRegistry()
    src = (
        'def register(marcel):\n'
        '    def contribute(event, ctx):\n'
        '        event.skill_paths.append("/zoo/skills/fromext/SKILL.md")\n'
        '        event.prompt_paths.append("/zoo/prompts/fromext.md")\n'
        '    marcel.on("resources_discover", contribute)\n'
    )
    load_extensions(_write_ext(tmp_path, 'extloader_rd', src), reg)

    event = await emit_resources_discover(reg)

    assert event.skill_paths == ['/zoo/skills/fromext/SKILL.md']
    assert reg.discovered_skill_paths == ['/zoo/skills/fromext/SKILL.md']
    assert reg.discovered_prompt_paths == ['/zoo/prompts/fromext.md']


async def test_emit_resources_discover_no_handlers_is_empty():
    reg = ExtensionRegistry()
    event = await emit_resources_discover(reg)
    assert event.skill_paths == []
    assert reg.discovered_skill_paths == []


def test_marcel_tool_and_api_tool_share_registry():
    """Both spellings are deprecation no-ops now — they warn and do not register."""
    import warnings

    from marcel_core.toolkit import marcel_tool

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')

        @marcel_tool('legacy.ping')
        async def ping(params, user_slug):
            return 'ok'

    assert any('connector' in str(w.message) for w in caught)
