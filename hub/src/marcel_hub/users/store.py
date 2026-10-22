"""The owner's row and the owner's first agent (buddy). There are no other people (ADR-013)."""

import re
import unicodedata

from sqlmodel import Session, col, select

from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.avatar.store import look_from_name
from marcel_hub.models import Agent, Conversation, ConversationKind, User

OWNER_AGENT_SLUG = 'marcel'
# Marcel's own look (ADR-008): the rose giraffe with the teal bow tie.
OWNER_LOOK = {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}
SLUG_MAX = 40


def ensure_owner(session: Session, name: str) -> User:
    """The owner's row, `usr_owner`, made if missing. Its name follows `MARCEL_OWNER_NAME`."""
    owner = session.get(User, OWNER_USER_ID)
    if owner is None:
        owner = User(id=OWNER_USER_ID, name=name)
    elif owner.name == name:
        return owner
    owner.name = name
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner


def slug_for(session: Session, name: str) -> str:
    """A free agent slug from its name: `Pip` → `pip`, then `pip-2`… Fixed once made."""
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    base = re.sub(r'[^a-z0-9]+', '-', ascii_name.lower()).strip('-')[:SLUG_MAX].strip('-')
    base = base or 'helper'
    slug, n = base, 1
    while session.exec(select(Agent).where(Agent.slug == slug)).first() is not None:
        n += 1
        slug = f'{base}-{n}'
    return slug


def _with_main_conversation(session: Session, agent: Agent) -> Agent:
    """Add `agent` and its main conversation in one commit, so the brain keeper (another
    process on the NUC) never sees the agent without it."""
    session.add(agent)
    session.flush()
    main = Conversation(agent_id=agent.id, kind=ConversationKind.main)
    session.add(main)
    session.flush()
    agent.main_conversation_id = main.id
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


def _set_look(agent: Agent, look: dict[str, str]) -> None:
    agent.look_animal = look['animal']
    agent.look_color = look['color']
    agent.look_personality = look['personality']
    agent.animal = look['animal']  # the contract's read-only copy of `look.animal`


def ensure_owner_agent(
    session: Session, name: str, persona_path: str | None = None
) -> tuple[Agent, bool]:
    """The owner's agent, and whether it was made now.

    An agent `marcel-hub agent create` made before `setup` existed is reused, not doubled: the
    owner's oldest live agent. It gets the slug `marcel` and Marcel's look when it has none (an
    agent made after migration 0010 has no slug).
    """
    found = session.exec(
        select(Agent)
        .where(Agent.owner_user_id == OWNER_USER_ID, col(Agent.archived_at).is_(None))
        .order_by(col(Agent.id))
    ).first()
    if found is not None:
        changed = False
        if found.slug is None:
            taken = session.exec(select(Agent).where(Agent.slug == OWNER_AGENT_SLUG)).first()
            found.slug = OWNER_AGENT_SLUG if taken is None else slug_for(session, found.name)
            changed = True
        if found.look_animal is None:
            _set_look(found, OWNER_LOOK)
            changed = True
        if found.main_conversation_id is None:
            return _with_main_conversation(session, found), False
        if changed:
            session.add(found)
            session.commit()
            session.refresh(found)
        return found, False
    made = Agent(
        owner_user_id=OWNER_USER_ID,
        name=name,
        animal=OWNER_LOOK['animal'],
        palette='marcel',
        seed=name,
        persona_path=persona_path,
        slug=OWNER_AGENT_SLUG
        if session.exec(select(Agent).where(Agent.slug == OWNER_AGENT_SLUG)).first() is None
        else slug_for(session, name),
    )
    _set_look(made, OWNER_LOOK)
    return _with_main_conversation(session, made), True


def give_missing_looks(session: Session) -> int:
    """At start-up (S-19.2): every agent without a look gets one, so each draws its own avatar.
    The owner's agent (the oldest live one) gets Marcel's look, as `setup` would give it; any other
    a look picked from its name. Returns how many changed."""
    looks_less = list(
        session.exec(select(Agent).where(col(Agent.look_animal).is_(None)).order_by(col(Agent.id)))
    )
    if not looks_less:
        return 0
    owners = live_agents(session, OWNER_USER_ID)
    owners_own = owners[0].id if owners else None
    for agent in looks_less:
        _set_look(agent, OWNER_LOOK if agent.id == owners_own else look_from_name(agent.name))
        session.add(agent)
    session.commit()
    return len(looks_less)


def live_agents(session: Session, user_id: str) -> list[Agent]:
    return list(
        session.exec(
            select(Agent)
            .where(Agent.owner_user_id == user_id, col(Agent.archived_at).is_(None))
            .order_by(col(Agent.id))
        ).all()
    )
