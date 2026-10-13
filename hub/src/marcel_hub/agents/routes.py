"""Agent settings (S-17.3, B-40, B-44): read and change one's own agent.

- `GET /api/agents/{agent_id}`: the contract's `Agent`, with its live state.
- `PATCH /api/agents/{agent_id}`: a new `name` (1 to 40 characters), a new `look` (the whole look;
  `animal` follows it), or `onboarded: true` (sets `onboarded_at` once). The deprecated `palette` is
  stored only while the agent has no look, and ignored after.

Only the caller's own agent: another person's answers 404 `agent_not_found`, exactly like one that
does not exist. A change sends `agent.updated` with the agent; a request that changes nothing sends
nothing. A rename also sends the agent's brain a `system_event` `agent_renamed` with `data: {name}`
(channel-protocol.md § Renames), so Marcel answers to the new name without a restart. The persona
file is not rewritten.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session

from marcel_hub.auth.deps import DbSession, current_device, require_device
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.routes import agent_json
from marcel_hub.channel.gateway import Gateway
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.models import Agent, utcnow

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


def _owned(session: Session, request: Request, agent_id: str) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None or agent.owner_user_id != current_device(request).user_id:
        raise ApiError(404, 'agent_not_found', 'That agent is not on this hub.')
    return agent


def _json(request: Request, agent: Agent) -> dict[str, Any]:
    return agent_json(agent, request.app.state.agent_states.current(agent.id))


@router.get('/agents/{agent_id}')
def get_agent(agent_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    return _json(request, _owned(session, request, agent_id))


@router.patch('/agents/{agent_id}')
def update_agent(
    agent_id: str, body: AgentUpdate, request: Request, session: DbSession
) -> dict[str, Any]:
    agent = _owned(session, request, agent_id)
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
    request.app.state.events.publish(
        session, agent.owner_user_id, 'agent.updated', {'agent': _json(request, agent)}
    )  # commits the change and its event together
    session.refresh(agent)
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
