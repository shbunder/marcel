"""A2UI component schema definitions and YAML parser.

Skills declare their UI components via co-located ``components.yaml`` files.
Each component is defined by a name, description, and JSON Schema props that
describe the structured data the frontend should render.

The agent emits ``{"component": "name", "props": {...}}`` — structured data,
not layout.  Each platform renders components using native widgets with a
generic fallback renderer for unimplemented components.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

log = logging.getLogger(__name__)


class ComponentSchema(BaseModel):
    """Schema for a single A2UI component declared by a skill."""

    name: str
    description: str = ''
    skill: str = ''
    props: dict[str, Any] = {}  # JSON Schema describing the component's props


class ComponentPayload(BaseModel):
    """Runtime A2UI payload emitted by the agent."""

    component: str
    props: dict[str, Any] = {}


def parse_components_yaml(path: Path, skill_name: str) -> list[ComponentSchema]:
    """Parse a ``components.yaml`` file and return validated component schemas.

    Expected format::

        components:
          - name: transaction_list
            description: List of bank transactions
            props:
              type: object
              properties:
                transactions:
                  type: array
                  ...

    Returns an empty list on parse errors (logged as warnings).
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except Exception:
        log.warning('Failed to parse %s', path, exc_info=True)
        return []

    if not isinstance(raw, dict):
        log.warning('components.yaml in %s is not a dict', skill_name)
        return []

    items = raw.get('components', [])
    if not isinstance(items, list):
        log.warning('components.yaml in %s: "components" is not a list', skill_name)
        return []

    schemas: list[ComponentSchema] = []
    for item in items:
        if not isinstance(item, dict) or 'name' not in item:
            log.warning('components.yaml in %s: skipping entry without name', skill_name)
            continue
        schemas.append(
            ComponentSchema(
                name=item['name'],
                description=item.get('description', ''),
                skill=skill_name,
                props=item.get('props', {}),
            )
        )

    return schemas
