"""`/api/approvals`: the owner's approval cards, one card by id, and answering one (B-12).

Only the owner of the task's agent sees or answers a card; anyone else gets 404, as for a card
that does not exist. A card that is no longer open answers 409 with the card as it stands.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from marcel_hub.auth import ApiError, current_device, require_device
from marcel_hub.channel.approvals import approval_json, owner_of_task
from marcel_hub.channel.gateway import Gateway
from marcel_hub.models import Approval, ApprovalState

router = APIRouter(prefix='/api', tags=['approvals'], dependencies=[Depends(require_device)])


class ApprovalAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: str = Field(pattern='^(approve|deny)$')
    note: str | None = Field(default=None, max_length=20000)


def _gateway(request: Request) -> Gateway:
    return request.app.state.channel


def not_found() -> ApiError:
    return ApiError(404, 'approval_not_found', 'That approval is not on this hub.')


@router.get('/approvals')
async def list_approvals(request: Request, state: ApprovalState | None = None) -> list[Any]:
    rows = _gateway(request).approvals.for_owner(current_device(request).user_id, state)
    return [approval_json(row) for row in rows]


def _own_approval(request: Request, approval_id: str) -> Approval:
    """The card, if it is on a task of the caller's; otherwise 404 `approval_not_found`."""
    gateway = _gateway(request)
    row = gateway.approvals.get(approval_id)
    if row is None:
        raise not_found()
    with Session(gateway.engine) as s:
        owner = owner_of_task(s, row.task_id)
    if owner != current_device(request).user_id:
        raise not_found()
    return row


@router.get('/approvals/{approval_id}')
async def get_approval(approval_id: str, request: Request) -> Any:
    """One card in any state, for a `marcel://approval/<id>` deep link."""
    return approval_json(_own_approval(request, approval_id))


@router.post('/approvals/{approval_id}')
async def answer_approval(approval_id: str, body: ApprovalAnswer, request: Request) -> Any:
    gateway = _gateway(request)
    _own_approval(request, approval_id)
    answered = await gateway.answer(approval_id, body.decision == 'approve', body.note)
    if answered is None:
        current = gateway.approvals.get(approval_id)
        assert current is not None
        return JSONResponse(approval_json(current), status_code=409)
    return approval_json(answered)
