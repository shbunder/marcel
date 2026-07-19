"""Job scoping resolution (FEAT-260718-49a01a).

A job template may declare ``skills:`` and ``connectors:`` — the exact
capability surface its AGENT-dispatch runs get. This module is the single
source of truth for resolving those names (FR4): the job tools call it at
save time so a typo fails while the author is still in the conversation,
and the executor calls it again at run time so a habitat removed since
save fails loud instead of silently running without its tools.

Resolution reuses the skills and connectors loaders, so the same rules
apply as everywhere else: the user-subfolder scoping chain picks the skill
document, and connector visibility is gated by the connector's ``scope``
against the **job user's** profile role — a non-admin user's job can no
more name an admin-scoped connector than that user could see it in
conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marcel_core.connectors.loader import ConnectorDoc
from marcel_core.skills.loader import SkillDoc


class JobScopingError(ValueError):
    """A job's ``skills:``/``connectors:`` declaration does not resolve.

    The message is user-facing (NFR3): it names what failed and lists the
    candidates that *do* exist, so the author can fix the declaration
    without leaving the conversation.
    """


@dataclass
class JobScope:
    """The resolved capability surface for one scoped job run."""

    skill_docs: list[SkillDoc] = field(default_factory=list)
    connector_docs: list[ConnectorDoc] = field(default_factory=list)


def _role_for(user_slug: str) -> str:
    from marcel_core.jobs import SYSTEM_USER
    from marcel_core.storage.users import get_user_role

    # System-scope runs have no profile; they resolve at the base role.
    if user_slug == SYSTEM_USER:
        return 'user'
    return get_user_role(user_slug)


def _unknown(kind: str, missing: list[str], available: list[str]) -> JobScopingError:
    names = ', '.join(sorted(missing))
    listing = ', '.join(sorted(available)) if available else '(none installed)'
    return JobScopingError(f'Unknown {kind}(s) for this job: {names}. Available {kind}s: {listing}.')


def resolve_job_scoping(user_slug: str, skills: list[str], connectors: list[str]) -> JobScope:
    """Resolve a job's declared skill and connector names to their documents.

    The catalogs are loaded for *user_slug* at that user's profile role, so
    scope rules apply exactly as they do in conversation. A skill's own
    ``marcel-connectors`` join the connector set implicitly — a skill's
    prose teaches tools, so a job that attaches the skill gets the tools it
    teaches without repeating the pairing (see the connector-skill-pairs
    rule).

    Raises:
        JobScopingError: A declared name matches nothing visible to the
            job's user; the message lists the candidates that exist.
    """
    from marcel_core.connectors.loader import load_connectors
    from marcel_core.jobs import SYSTEM_USER
    from marcel_core.skills.loader import load_skills

    role = _role_for(user_slug)
    scope = JobScope()

    # System-scope jobs have no user, so only global habitats apply — and the
    # reserved ``_system`` slug must never be joined into a per-user path.
    lookup = None if user_slug == SYSTEM_USER else user_slug

    skill_map = {doc.name: doc for doc in load_skills(lookup, role)}
    missing_skills = [name for name in skills if name not in skill_map]
    if missing_skills:
        raise _unknown('skill', missing_skills, list(skill_map))
    scope.skill_docs = [skill_map[name] for name in skills]

    wanted = list(connectors)
    for doc in scope.skill_docs:
        wanted.extend(c for c in doc.connectors if c not in wanted)

    if wanted:
        connector_map = {doc.config.name: doc for doc in load_connectors(lookup, role)}
        missing_connectors = [name for name in wanted if name not in connector_map]
        if missing_connectors:
            raise _unknown('connector', missing_connectors, list(connector_map))
        scope.connector_docs = [connector_map[name] for name in wanted]

    return scope
