"""iCloud integration — exposes Apple calendar and mail as MCP tools."""

from .tool import build_icloud_mcp_server

__all__ = ['build_icloud_mcp_server']
