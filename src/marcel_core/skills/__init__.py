"""Skills registry and integration tool for the Marcel agent."""

from .loader import load_skills
from .registry import get_skill, list_skills

__all__ = ['get_skill', 'list_skills', 'load_skills']
