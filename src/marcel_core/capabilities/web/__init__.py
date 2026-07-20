"""The ``web`` capability — search, navigate, and interact with the web.

A single pydantic-ai ``web`` tool that dispatches to many actions, contributed
as a **non-deferred** capability (:func:`build_web_capability`) rather than a
flat-registry entry. The search/browser/SSRF domain code lives in this package
and has no consumer outside the agent run — the same shape shell/file tools had
before ``capabilities/execution/`` (ADR-260720-9318b1).

Non-deferred is load-bearing: CodeMode wraps tools from the combined toolset
(capability-owned included) but keeps *deferred* tools native, so a non-deferred
web stays foldable into ``run_code`` exactly as before
(``CODE_MODE_ELIGIBLE={'web'}``). The capability id is ``web-tools``, never
``web`` — a ``web`` **skill** habitat already claims that id, and duplicate
capability ids crash the agent build (STORY-260719-9207ca).

See :func:`marcel_core.capabilities.web.dispatcher.web` for the action list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marcel_core.capabilities.web.dispatcher import web

if TYPE_CHECKING:
    from pydantic_ai.capabilities import Capability

    from marcel_core.harness.context import MarcelDeps

# The tool name the model sees, and the CODE_MODE / tool_filter key.
WEB_TOOL_NAME = 'web'

__all__ = ['WEB_TOOL_NAME', 'build_web_capability', 'web']


def build_web_capability() -> 'Capability[MarcelDeps]':
    """The web capability for one build — one ``web`` tool, non-deferred."""
    from pydantic_ai.capabilities import Capability

    return Capability(
        id='web-tools',
        description='Search the web and drive a headless browser via the `web` tool.',
        tools=[web],
        defer_loading=False,
    )
