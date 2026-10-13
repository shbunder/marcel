"""The owner's row, the owner's agent, and the family members the owner adds or removes."""

import re
import unicodedata
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Device,
    PairingCode,
    Task,
    User,
    UserRole,
    utcnow,
)
from marcel_hub.tasks.states import FINISHED

OWNER_AGENT_SLUG = 'marcel'
# Marcel's own look (ADR-008): the rose giraffe with the teal bow tie.
OWNER_LOOK = {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}
SLUG_MAX = 40


def ensure_owner(session: Session, name: str) -> User:
    """The owner's row, `usr_owner`, made if missing. Its name follows `MARCEL_OWNER_NAME`."""
    owner = session.get(User, OWNER_USER_ID)
    if owner is None:
        owner = User(id=OWNER_USER_ID, name=name, role=UserRole.owner)
    elif owner.name == name and owner.role == UserRole.owner:
        return owner
    owner.name = name
    owner.role = UserRole.owner
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner


def owner_name(session: Session, fallback: str) -> str:
    owner = session.get(User, OWNER_USER_ID)
    return owner.name if owner is not None else fallback


def role_of(session: Session, user_id: str) -> UserRole:
    """A device's person's role. The owner's id is the owner even before its row exists."""
    if user_id == OWNER_USER_ID:
        return UserRole.owner
    user = session.get(User, user_id)
    return user.role if user is not None else UserRole.member


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


def add_member(session: Session, name: str, agent_name: str, look: dict[str, str] | None) -> User:
    """A family member and their own agent, main conversation and Inbox (B-38). Their brain
    workspace comes with S-17.7; a look picked from the name when none is given, with S-19.2."""
    user = User(name=name, role=UserRole.member)
    session.add(user)
    session.flush()
    agent = Agent(
        owner_user_id=user.id,
        name=agent_name,
        animal=look['animal'] if look else 'giraffe',
        palette='marcel',
        seed=agent_name,
        slug=slug_for(session, agent_name),
    )
    if look is not None:
        _set_look(agent, look)
    _with_main_conversation(session, agent)
    session.refresh(user)
    return user


def get_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise ApiError(404, 'user_not_found', 'That person is not on this Marcel.')
    return user


def live_agents(session: Session, user_id: str) -> list[Agent]:
    return list(
        session.exec(
            select(Agent)
            .where(Agent.owner_user_id == user_id, col(Agent.archived_at).is_(None))
            .order_by(col(Agent.id))
        ).all()
    )


def unfinished_tasks(session: Session, user_id: str) -> list[str]:
    """The ids of the person's quests that are still queued or running."""
    agent_ids = [a.id for a in session.exec(select(Agent).where(Agent.owner_user_id == user_id))]
    if not agent_ids:
        return []
    rows = session.exec(
        select(Task.id).where(col(Task.agent_id).in_(agent_ids), col(Task.state).not_in(FINISHED))
    ).all()
    return list(rows)


def refuse_owner(user: User) -> None:
    if user.role == UserRole.owner or user.id == OWNER_USER_ID:
        raise ApiError(
            409, 'cannot_remove_owner', 'The owner cannot be removed. Nothing was changed.'
        )


def sign_out(session: Session, user: User) -> None:
    """Their phones out at once (their tokens answer 401), and their unused pairing codes gone."""
    session.exec(delete(Device).where(col(Device.user_id) == user.id))
    session.exec(delete(PairingCode).where(col(PairingCode.user_id) == user.id))
    session.commit()


def forget(session: Session, user: User) -> None:
    """Archive their agents and forget the person. Their chats, items and notes stay."""
    now = utcnow()
    for agent in session.exec(select(Agent).where(Agent.owner_user_id == user.id)):
        if agent.archived_at is None:
            agent.archived_at = now
            session.add(agent)
    session.delete(user)
    session.commit()


def user_json(session: Session, user: User) -> dict[str, Any]:
    """The contract's `User`, as the owner's People screen shows it."""
    body: dict[str, Any] = {
        'id': user.id,
        'name': user.name,
        'role': user.role.value,
        'created_at': user.created_at.isoformat(),
        'device_count': session.exec(
            select(func.count()).select_from(Device).where(col(Device.user_id) == user.id)
        ).one(),
        'quests_this_week': 0,
    }
    agents = live_agents(session, user.id)
    if agents:
        agent = agents[0]
        helper: dict[str, Any] = {'id': agent.id, 'name': agent.name}
        if agent.look_animal and agent.look_color and agent.look_personality:
            helper['look'] = {
                'animal': agent.look_animal,
                'color': agent.look_color,
                'personality': agent.look_personality,
            }
        body['agent'] = helper
        since = utcnow() - timedelta(days=7)
        body['quests_this_week'] = session.exec(
            select(func.count())
            .select_from(Task)
            .where(col(Task.agent_id).in_([a.id for a in agents]), col(Task.created_at) >= since)
        ).one()
    return body


def all_users(session: Session) -> list[User]:
    """The owner first, then members by name."""
    users = session.exec(select(User)).all()
    return sorted(users, key=lambda u: (u.role != UserRole.owner, u.name.lower(), u.id))
