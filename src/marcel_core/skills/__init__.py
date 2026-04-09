"""Skills registry and integration tool for the Marcel agent."""

from .loader import load_skills
from .registry import get_skill, list_skills
from .tool import build_skills_mcp_server

__all__ = ['build_skills_mcp_server', 'get_skill', 'list_skills', 'load_skills']
