"""`POST /channel/session-end` (session-api.yaml `sessionEnded`, S-05.8, ADR-010): the marcel
plugin's `Stop` and `SessionEnd` hooks tell the hub a session is finishing or has ended.

Outside `/api` like `/channel` (the tunnel never forwards it; a request with the tunnel's
`cf-connecting-ip` header answers 404), with the channel's identity: the hub secret in
`X-Marcel-Secret` and `CLAUDE_CODE_SESSION_ID` in `X-Marcel-Session`, known the same way a channel
`hello` is (401 `bad_secret` / `unknown_session`, 503 `no_secret` with no secret on the hub).

- `report_owed` is true only for a worker whose task is `working` or `needs_you`: a `done` or
  `failed` report moves the task out of those, and a follow-up moves it back (task-states.md).
  For a brain, side-thread or adopted session it is always false.
- `stop`: only the answer; the `Stop` hook reminds the worker once to report.
- `session_end` from a worker that owed a report, or whose task was `silent`: the task goes to
  `failed`, reason
  `ended_without_report` with the hook's own end reason in plain words. The channel gateway sees
  the state change and the triage tells the brain (`task_failed`), like any failed task. A task
  that already ended (the worker reported, or the runner's poll got there first) only has the end
  reason logged: no task event fits it without a contract change.
"""

import contextlib
import hmac
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from marcel_hub.auth.errors import ApiError
from marcel_hub.channel.gateway import Gateway
from marcel_hub.models import TaskState
from marcel_hub.tasks.states import IllegalTransition

log = logging.getLogger(__name__)

TUNNEL_HEADER = 'cf-connecting-ip'
OWING = (TaskState.working, TaskState.needs_you)
# A session that ends is also the sign a `silent` task waited for (B-23): it fails too
# (task-states.md: `working, needs_you, silent → failed`), though `report_owed` stays false for it.
ENDING_FAILS = (*OWING, TaskState.silent)

router = APIRouter()


class SessionEnd(BaseModel):
    model_config = ConfigDict(extra='forbid')

    event: Literal['stop', 'session_end']
    reason: Annotated[str, Field(min_length=1, max_length=64)] | None = None


async def _body(request: Request) -> SessionEnd:
    try:
        return SessionEnd.model_validate(await request.json())
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
    except ValueError as exc:  # not JSON at all
        raise RequestValidationError([{'loc': ('body',), 'msg': str(exc)}]) from None


@router.post('/channel/session-end')
async def session_ended(request: Request) -> dict[str, bool]:
    """Who is asking is checked before the body is read: a caller without the secret learns
    nothing about the body (401, never 400)."""
    if TUNNEL_HEADER in request.headers:
        raise ApiError(404, 'not_found', 'Not found.')
    gateway: Gateway = request.app.state.channel
    secret = gateway._secret()  # the channel's own check: the file, at least MIN_SECRET long
    if secret is None:
        raise ApiError(503, 'no_secret', 'The hub has no secret set up. Redeploy Marcel.')
    given = request.headers.get('x-marcel-secret', '')
    if not hmac.compare_digest(secret.encode(), given.encode()):
        raise ApiError(401, 'bad_secret', 'The secret does not match this hub.')
    session_id = request.headers.get('x-marcel-session', '')
    peer = gateway.resolve(session_id) if session_id else None
    if peer is None:
        raise ApiError(
            401, 'unknown_session', 'No task or conversation is waiting for this session.'
        )
    body = await _body(request)
    if peer.role != 'worker' or peer.task_id is None:
        if body.event == 'session_end':
            log.info('Session %s (%s) ended: %s.', session_id[:8], peer.role, body.reason or '?')
        return {'report_owed': False}
    registry = gateway.tasks()
    task = registry.get(peer.task_id)
    owed = task.state in OWING
    if body.event == 'stop':
        return {'report_owed': owed}
    why = body.reason or 'no reason given'
    if task.state not in ENDING_FAILS:
        log.info('The session of task %s ended (%s); the task is already %s.',
                 task.id, why, task.state.value)  # fmt: skip
        return {'report_owed': False}
    log.warning('Task %s: its session ended (%s) without a report; the task failed.', task.id, why)
    reason = f'ended_without_report: the session ended ({why}) without reporting done or failed'
    with contextlib.suppress(IllegalTransition):  # moved meanwhile; the registry logged it
        await registry.report_state(task.id, TaskState.failed, reason)
    return {'report_owed': owed}
