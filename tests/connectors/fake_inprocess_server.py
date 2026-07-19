"""A bundled in-process connector server, standing in for a real habitat.

Built on odile's :class:`~odile.FakeMCPServer` rather than a bare FastMCP, so
marcel actually consumes the scenario construct odile ships (and gets call
recording for free — ``SERVER.calls('now')`` shows what the agent passed).
The module-level ``mcp`` is the FastMCP instance the connector loader expects.
"""

from odile import FakeMCPServer

SERVER = FakeMCPServer('clock').returns('now', '2026-07-19T00:00:00Z', description='Return a fixed timestamp')

mcp = SERVER.server
