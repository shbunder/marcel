"""Buddies (S-17.3, S-22.27, B-40, B-44, B-49): add, read, change and archive them.

- `POST /api/agents`: a new buddy with its main conversation and Inbox; its look is picked from
  its name when none is sent. The brain keeper starts its brain on its next check (poked now).
- `GET /api/agents/{agent_id}`: the contract's `Agent`, with its live state; an archived buddy
  too, with `archived_at`.
- `PATCH /api/agents/{agent_id}`: a new `name` (1 to 40 characters), a new `look` (the whole look;
  `animal` follows it), or `onboarded: true` (sets `onboarded_at` once). The deprecated `palette` is
  stored only while the agent has no look, and ignored after.
- `POST /api/agents/{agent_id}/archive`: the buddy's brain stops (the keeper retires it), and its
  schedules and digests with it; phones that showed it move to the first live buddy, each with
  `device.updated`. 409 `last_buddy` for the only live one. Archiving again changes nothing.

Any buddy of the one person (ADR-013); an unknown id answers 404 `agent_not_found`. A change sends
`agent.updated` with the agent and its `avatar_etag` (the `ETag` of
`GET /api/agents/{agent_id}/avatar`, `avatar/`); a request that changes nothing sends
nothing. A rename also sends the agent's brain a `system_event` `agent_renamed` with `data: {name}`
(channel-protocol.md § Renames), so Marcel answers to the new name without a restart. The persona
file is not rewritten.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, col, select

from marcel_hub.auth.deps import DbSession, require_device
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.routes import agent_json, show_buddy
from marcel_hub.auth.tokens import OWNER_USER_ID, first_buddy
from marcel_hub.avatar.store import etag as avatar_etag
from marcel_hub.avatar.store import look_from_name, look_of
from marcel_hub.channel.gateway import Gateway
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.models import Agent, Conversation, ConversationKind, Device, inbox_id, utcnow
from marcel_hub.users.store import slug_for

LookAnimal = Literal[
    'giraffe', 'crocodile', 'frog', 'cat', 'fox', 'bear', 'panda', 'rabbit', 'dog', 'pig', 'mouse'
]
LookColor = Literal[
    'natural', 'rose', 'coral', 'tangerine', 'honey', 'lime', 'green', 'teal', 'sky', 'blue',
    'lavender', 'plum', 'cocoa', 'sand', 'slate', 'snow', 'ink',
]  # fmt: skip
LookPersonality = Literal[
    'professional', 'cheerful', 'curious', 'cozy', 'bold', 'cool', 'sporty', 'shy', 'grumpy'
]


class Look(BaseModel):
    model_config = ConfigDict(extra='forbid')

    animal: LookAnimal
    color: LookColor
    personality: LookPersonality


class NewAgent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: Annotated[str, Field(min_length=1, max_length=40)]
    look: Look | None = None


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    look: Look | None = None
    onboarded: Literal[True] | None = None
    palette: str | None = None  # deprecated in 0.7.0

    @model_validator(mode='after')
    def _something(self) -> 'AgentUpdate':
        if not self.model_fields_set:
            raise ValueError('send at least one of name, look, onboarded, palette')
        sent_null = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if sent_null:  # the contract has no nullable field here
            raise ValueError(f'{", ".join(sent_null)} cannot be null')
        return self


router = APIRouter(prefix='/api', tags=['agents'], dependencies=[Depends(require_device)])


def owned_agent(session: Session, request: Request, agent_id: str) -> Agent:
    """The buddy `agent_id`, or 404 `agent_not_found`. Every buddy is the one person's."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, 'agent_not_found', 'That agent is not on this hub.')
    return agent


def _json(request: Request, agent: Agent) -> dict[str, Any]:
    return agent_json(agent, request.app.state.agent_states.current(agent.id))


def _announce(request: Request, session: Session, agent: Agent) -> None:
    """`agent.updated` for every phone, committed with whatever `session` holds."""
    data = {'agent': _json(request, agent), 'avatar_etag': avatar_etag(look_of(agent))}
    request.app.state.events.publish(session, agent.owner_user_id, 'agent.updated', data)
    session.refresh(agent)


def _poke_brains(request: Request) -> None:
    """Have the brain keeper check now: it starts a new buddy's brain, stops an archived one's."""
    keeper = getattr(request.app.state, 'brain', None)
    if keeper is not None:
        keeper.poke()


@router.post('/agents', status_code=201)
def create_agent(body: NewAgent, request: Request, session: DbSession) -> dict[str, Any]:
    look = body.look.model_dump() if body.look is not None else look_from_name(body.name)
    agent = Agent(
        owner_user_id=OWNER_USER_ID,
        name=body.name,
        slug=slug_for(session, body.name),  # its own brain folder and memory (S-17.7)
        animal=look['animal'],
        palette='marcel',
        seed=body.name,
        look_animal=look['animal'],
        look_color=look['color'],
        look_personality=look['personality'],
    )
    session.add(agent)
    session.flush()
    main = Conversation(agent_id=agent.id, kind=ConversationKind.main)
    session.add(main)
    session.flush()
    agent.main_conversation_id = main.id
    session.add(agent)
    inbox_id(session.connection(), agent.id)
    # The buddy, its main conversation, its Inbox and the event in one commit, so the brain
    # keeper never sees the buddy without its conversation.
    _announce(request, session, agent)
    _poke_brains(request)
    return _json(request, agent)


@router.post('/agents/{agent_id}/archive')
def archive_agent(agent_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    agent = owned_agent(session, request, agent_id)
    if agent.archived_at is not None:
        return _json(request, agent)  # archived already: nothing changes
    others = session.exec(
        select(Agent.id).where(
            col(Agent.archived_at).is_(None),
            col(Agent.main_conversation_id).is_not(None),
            Agent.id != agent.id,
        )
    ).first()
    if others is None:
        raise ApiError(
            409,
            'last_buddy',
            f'{agent.name} is your only buddy, and Marcel always keeps one. Add another first.',
        )
    agent.archived_at = utcnow()
    session.add(agent)
    _announce(request, session, agent)
    fallback = first_buddy(session)
    if fallback is not None:
        for device in session.exec(select(Device).where(Device.agent_id == agent.id)).all():
            show_buddy(request, session, device, fallback)
    _poke_brains(request)
    return _json(request, agent)


@router.get('/agents/{agent_id}')
def get_agent(agent_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    return _json(request, owned_agent(session, request, agent_id))


@router.patch('/agents/{agent_id}')
def update_agent(
    agent_id: str, body: AgentUpdate, request: Request, session: DbSession
) -> dict[str, Any]:
    agent = owned_agent(session, request, agent_id)
    before = agent.model_dump()
    renamed = body.name is not None and body.name != agent.name
    if body.name is not None:
        agent.name = body.name
    if body.look is not None:
        agent.look_animal, agent.look_color = body.look.animal, body.look.color
        agent.look_personality = body.look.personality
        agent.animal = body.look.animal  # `animal` follows the look
    if body.palette is not None and agent.look_color is None:
        agent.palette = body.palette  # deprecated: only while the agent has no look
    if body.onboarded and agent.onboarded_at is None:
        agent.onboarded_at = utcnow()
    if agent.model_dump() == before:
        return _json(request, agent)  # nothing changed: no event, no frame
    session.add(agent)
    _announce(request, session, agent)  # the change and its event are committed together
    if renamed and agent.main_conversation_id is not None:
        gateway: Gateway = request.app.state.channel
        gateway.outbox.put(
            conversation_target(agent.main_conversation_id),
            {
                'type': 'system_event',
                'kind': 'agent_renamed',
                'text': f'Your name is now {agent.name}. Answer to it from now on.',
                'data': {'name': agent.name},
            },
        )
    return _json(request, agent)
