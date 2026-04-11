"""A2UI component registry — aggregates component schemas from all skills.

The registry is built at startup by loading all skills and collecting their
``components.yaml`` declarations.  It provides a flat namespace of component
schemas that frontends can query via the ``/api/components`` endpoint.
"""

from __future__ import annotations

import logging

from marcel_core.skills.components import ComponentSchema
from marcel_core.skills.loader import load_skills

log = logging.getLogger(__name__)


class ComponentRegistry:
    """In-memory index of all A2UI component schemas across skills."""

    def __init__(self, components: list[ComponentSchema]) -> None:
        self._by_name: dict[str, ComponentSchema] = {}
        for c in components:
            if c.name in self._by_name:
                log.warning(
                    'Component name collision: %r declared by skill %r shadows %r',
                    c.name,
                    c.skill,
                    self._by_name[c.name].skill,
                )
            self._by_name[c.name] = c

    def get(self, name: str) -> ComponentSchema | None:
        return self._by_name.get(name)

    def list_all(self) -> list[ComponentSchema]:
        return list(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)


def build_registry(user_slug: str) -> ComponentRegistry:
    """Build a component registry from all loaded skills for the given user."""
    skills = load_skills(user_slug)
    all_components: list[ComponentSchema] = []
    for skill in skills:
        all_components.extend(skill.components)
    log.info('Component registry built: %d components from %d skills', len(all_components), len(skills))
    return ComponentRegistry(all_components)
