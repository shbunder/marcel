"""Skill → deferred capability factory (FEAT-260718-85b545).

Each visible skill becomes a pydantic-ai ``Capability`` with
``defer_loading=True``: the catalog shows only its ``id`` (the skill name)
and ``description`` (~100 tokens), the framework-managed ``load_capability``
tool returns the SKILL.md body as the capability's instructions, and a
skill-root-scoped ``read_skill_resource`` tool (owned by the capability, so
it only appears once the skill is loaded) serves the skill's resource files.

This is the standard's three-tier progressive disclosure implemented
natively — replacing the hand-rolled skill-index prompt block and the
``marcel(action="read_skill")`` action.

A skill named as the turn's ``/<skill>`` slash override is built **eager**
(``defer_loading=False``) so its body is in the prompt from the first
request, mirroring the pre-v2 force-load.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from marcel_core.harness.context import MarcelDeps
from marcel_core.skills.loader import SkillDoc, get_skill_resource, list_skill_resources


def _skill_capability(doc: SkillDoc, *, eager: bool) -> Capability[MarcelDeps]:
    skill_name = doc.name
    description = doc.description + (' — needs setup' if doc.is_setup else '')

    async def read_skill_resource(ctx: RunContext[MarcelDeps], resource: str) -> str:
        """Read a named resource file bundled with this skill.

        `resource` is a filename or stem (e.g. "feeds.yaml" or "feeds").
        Only files inside the skill's own directory are reachable.
        """
        content = get_skill_resource(skill_name, resource, ctx.deps.user_slug)
        if content is None:
            available = list_skill_resources(skill_name, ctx.deps.user_slug)
            hint = f' Available: {", ".join(available)}.' if available else ''
            return f'No resource {resource!r} in skill {skill_name!r}.{hint}'
        return content

    return Capability(
        id=skill_name,
        description=description,
        instructions=doc.content,
        tools=[read_skill_resource],
        defer_loading=not eager,
    )


def build_skill_capabilities(
    user_slug: str,
    role: str = 'user',
    *,
    eager_skill: str | None = None,
) -> list[Capability[MarcelDeps]]:
    """The deferred skill capabilities for *user_slug* at *role*.

    ``eager_skill`` (the turn's ``/<skill>`` override) is built non-deferred
    so its body is present from the first model request.
    """
    from marcel_core.skills.loader import load_skills

    return [_skill_capability(doc, eager=(doc.name == eager_skill)) for doc in load_skills(user_slug, role)]
