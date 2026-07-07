"""Tests for the marcel_sdk extension contract + the import wall."""

from __future__ import annotations

import marcel_sdk
from marcel_sdk.extension import ExtensionAPI, ToolResult


def test_tool_result_defaults():
    r = ToolResult(text='ok')
    assert r.text == 'ok'
    assert r.is_error is False


def test_tool_result_error():
    r = ToolResult(text='boom', is_error=True)
    assert r.is_error is True


def test_extension_api_is_runtime_checkable():
    """A concrete object with the right methods satisfies the protocol."""

    class Stub:
        def tool(self, name):
            return lambda fn: fn

        def on(self, event, handler):
            pass

        def channel(self, plugin):
            pass

        def skill(self, source):
            pass

        def job(self, source):
            pass

        def agent(self, source):
            pass

        def command(self, name, handler):
            pass

    assert isinstance(Stub(), ExtensionAPI)


def test_incomplete_object_is_not_extension_api():
    class Partial:
        def tool(self, name):
            return lambda fn: fn

    assert not isinstance(Partial(), ExtensionAPI)


# ---------------------------------------------------------------------------
# the import wall — marcel_sdk/__init__
# ---------------------------------------------------------------------------


def test_version_present_and_independent():
    assert isinstance(marcel_sdk.__version__, str)
    assert marcel_sdk.__version__  # non-empty


def test_eager_contracts_exported():
    # Pure contracts are importable directly off the package.
    assert marcel_sdk.EventBus is not None
    assert marcel_sdk.ExtensionAPI is not None
    assert marcel_sdk.ToolResult is not None
    assert marcel_sdk.ToolCallEvent.NAME == 'tool_call'


def test_lazy_helpers_resolve_from_kernel():
    """Kernel-backed helpers resolve on attribute access (no import cycle)."""
    # get_logger + marcel_tool are callables re-exported from marcel_core.plugin.
    logger = marcel_sdk.get_logger('demo')
    assert logger.name == 'demo'
    assert callable(marcel_sdk.marcel_tool)
    # credentials / paths / models / rss are modules.
    assert marcel_sdk.credentials is not None
    assert hasattr(marcel_sdk.paths, 'cache_dir')


def test_unknown_attribute_raises():
    import pytest

    with pytest.raises(AttributeError):
        _ = marcel_sdk.does_not_exist


def test_all_names_are_resolvable():
    """Everything in __all__ resolves (eager or lazy)."""
    for name in marcel_sdk.__all__:
        assert getattr(marcel_sdk, name) is not None
