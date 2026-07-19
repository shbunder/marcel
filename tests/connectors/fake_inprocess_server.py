"""A minimal bundled in-process FastMCP server, standing in for a connector habitat."""

from fastmcp import FastMCP

mcp = FastMCP('clock')


@mcp.tool
def now() -> str:
    """Return a fixed timestamp."""
    return '2026-07-19T00:00:00Z'
