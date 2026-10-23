"""`/api/approvals`: the approval cards, one card by id, and answering one (B-12).

Any paired phone sees and answers every card, a self-change card too (ADR-013: one person, many
buddies). An id that is no card answers 404. A card that is no longer open answers 409 with the
card as it stands. A self-change card (S-11.1) is answered by `selfmod.SelfChanges`, never sent
to a session.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from marcel_hub.auth import ApiError, require_device
from marcel_hub.channel.approvals import approval_json, is_self_change
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
    """Every buddy's cards, open ones first."""
    return [approval_json(row) for row in _gateway(request).approvals.cards(state)]


def _card(request: Request, approval_id: str) -> Approval:
    """The card, or 404 `approval_not_found`."""
    row = _gateway(request).approvals.get(approval_id)
    if row is None:
        raise not_found()
    return row


@router.get('/approvals/{approval_id}')
async def get_approval(approval_id: str, request: Request) -> Any:
    """One card in any state, for a `marcel://approval/<id>` deep link."""
    return approval_json(_card(request, approval_id))


@router.post('/approvals/{approval_id}')
async def answer_approval(approval_id: str, body: ApprovalAnswer, request: Request) -> Any:
    gateway = _gateway(request)
    card = _card(request, approval_id)
    approve = body.decision == 'approve'
    if is_self_change(card):
        answered = await request.app.state.selfmod.answer(approval_id, approve, body.note)
    else:
        answered = await gateway.answer(approval_id, approve, body.note)
    if answered is None:
        current = gateway.approvals.get(approval_id)
        assert current is not None
        return JSONResponse(approval_json(current), status_code=409)
    return approval_json(answered)
