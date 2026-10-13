"""`POST /api/session/report` (contracts/session-api.yaml, S-07.2): a cloud worker's reports.

A cloud worker cannot reach the channel, so it reports here, with the per-task token from its
prompt (S-07.1, `cloud.py`). This module is only the door: `CloudTasks.task_for_token` says which
task a token is for, and `CloudTasks.report` stores the report exactly as a NUC worker's channel
`report` (progress with `next`, artifacts with their cards, notes, the B-06 milestone).

- **The token first.** Before the body is read, the token must name a live task: unknown, expired
  (48 h), or of a task that is `done`, `failed` or `stopped` is the hub's own 404 for an unknown
  address, word for word, even for a broken body. A token is looked up by its sha256, so the
  lookup's time does not depend on how much of a guess was right. The report lands on the token's
  task; nothing in the body can name another one.
- **Wrong tokens are limited per caller** (Cloudflare's `cf-connecting-ip`, else the peer): past
  `MAX_WRONG_TOKENS` in a minute that caller's wrong tokens get 429 `too_many_requests` (the
  contract's answer for this route), and past `WRONG_TOKEN_CEILING` in a minute from everyone
  together all wrong tokens do. A good token always gets in. Each lockout is said once in the log.
- **Good tokens are limited per task**: past `MAX_REPORTS` in a minute a task's reports get 429
  `too_many_reports`, so a worker stuck in a loop cannot flood the owner's phone.
- **Hidden.** Any other method on the address, or a trailing slash, is the same 404.
- **Size.** A body over `MAX_BODY` is refused (400 `bad_report`) before it is parsed.
- **A retried report is taken once.** The contract has no report id, so the hub takes the same
  report (the same JSON, key order aside) from the same task once in `REPEAT_WINDOW`; a repeat is
  answered 202 and stores nothing. A `done` or `failed` report spends the token, so its retry is a
  404. The memory of recent reports is lost on a hub restart.
- **Answers.** 202 `{task_id}`. 400 `bad_note`, `bad_next` or `bad_report` say what to change; 409
  `too_many_notes` / `notes_full` and 503 `memory_unavailable` tell the worker to put the fact in
  its done summary instead. Nothing is stored for any refusal.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from marcel_hub.adopt import HIDDEN_MESSAGE, caller_of, hidden
from marcel_hub.auth.errors import ApiError, error_response
from marcel_hub.channel.gateway import NOTE_FALLBACK
from marcel_hub.cloud import TOKEN_PREFIX, BadReport, CloudTasks, ReportRefused

log = logging.getLogger(__name__)

REPORT_PATH = '/api/session/report'
MAX_BODY = 64 * 1024  # bytes; a done report with its artifacts is a few kB
MAX_WRONG_TOKENS = 10  # per caller, per minute
WRONG_TOKEN_CEILING = 1000  # wrong tokens per minute from everyone together
MAX_REPORTS = 30  # accepted reports per task, per minute
WINDOW = 60.0  # seconds
MAX_CALLERS = 10_000  # callers remembered at once; past it only the overall ceiling counts
REPEAT_WINDOW = 600.0  # seconds a report is remembered, to take a retry once
MAX_REMEMBERED = 50  # recent reports remembered per task

# What the gateway's refusals are at this route.
STATUS = {
    'bad_note': 400,
    'bad_next': 400,
    'unknown_artifact': 400,
    'too_many_notes': 409,
    'notes_full': 409,
    'memory_unavailable': 503,
}


def _recent(times: list[float], now: float, window: float = WINDOW) -> list[float]:
    return [t for t in times if now - t < window]


class ReportGate:
    """The route's controls: who may report, how often, and which reports were already taken."""

    def __init__(self, cloud: CloudTasks, clock: Callable[[], float] = time.monotonic) -> None:
        self.cloud = cloud
        self.clock = clock
        self.failures: dict[str, list[float]] = {}
        self.overall: list[float] = []
        self.accepted: dict[str, list[float]] = {}
        self.seen: dict[str, dict[str, float]] = {}
        self._said_locked: set[str] = set()

    # Tokens ---------------------------------------------------------------------------------

    def task_for(self, authorization: str | None, caller: str) -> str:
        """The task the bearer token is for; otherwise count it and raise 404 (or 429)."""
        scheme, _, token = (authorization or '').partition(' ')
        token = token.strip()
        if scheme.lower() == 'bearer' and token.startswith(TOKEN_PREFIX):
            task_id = self.cloud.task_for_token(token)
            if task_id is not None:
                return task_id
        now = self.clock()
        self.overall = _recent(self.overall, now)
        mine = _recent(self.failures.get(caller, []), now)
        if len(mine) >= MAX_WRONG_TOKENS or len(self.overall) >= WRONG_TOKEN_CEILING:
            if caller not in self._said_locked:
                self._said_locked.add(caller)
                log.warning(
                    'Too many wrong report tokens from %s (or overall) in the last minute: '
                    'answering 429 for a minute. Good tokens still work.',
                    caller,
                )
            raise ApiError(
                429,
                'too_many_requests',
                'Too many wrong report tokens from here. Wait a minute and try again.',
                {'Retry-After': '60'},
            )
        self._said_locked.discard(caller)
        self._forget_quiet_callers(now)  # first, so a full map makes room
        if caller in self.failures or len(self.failures) < MAX_CALLERS:
            self.failures[caller] = [*mine, now]
        self.overall.append(now)
        raise hidden()

    def _forget_quiet_callers(self, now: float) -> None:
        for caller in [c for c, times in self.failures.items() if not _recent(times, now)]:
            del self.failures[caller]

    # Reports --------------------------------------------------------------------------------

    def _limit(self, task_id: str, now: float) -> None:
        recent = _recent(self.accepted.get(task_id, []), now)
        self.accepted[task_id] = recent
        if len(recent) >= MAX_REPORTS:
            raise ApiError(
                429,
                'too_many_reports',
                f'This task sent {MAX_REPORTS} reports in the last minute, the most it may. '
                'Report milestones, not every step; wait a minute and try again.',
                {'Retry-After': '60'},
            )

    @staticmethod
    def _key(body: Any) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    async def take(self, task_id: str, body: Any) -> None:
        """Store one report, once; raise the contract's error for one that is refused."""
        now = self.clock()
        seen = {k: t for k, t in self.seen.get(task_id, {}).items() if now - t < REPEAT_WINDOW}
        self.seen[task_id] = seen
        key = self._key(body)
        if key in seen:
            log.info('Task %s sent the same report again; it was taken once.', task_id)
            return
        self._limit(task_id, now)
        # Marked before the await: the same report arriving twice at once is still taken once.
        seen[key] = now
        try:
            await self.cloud.report(task_id, body)
        except BadReport as exc:
            seen.pop(key, None)
            raise ApiError(400, _bad_code(body, str(exc)), _bad_message(body, str(exc))) from None
        except ReportRefused as exc:
            seen.pop(key, None)
            raise ApiError(STATUS.get(exc.code, 409), exc.code, exc.message) from None
        self.accepted[task_id].append(now)
        if len(seen) > MAX_REMEMBERED:
            del seen[min(seen, key=seen.__getitem__)]


def _kind(body: Any) -> Any:
    return body.get('kind') if isinstance(body, dict) else None


def _bad_code(body: Any, problem: str) -> str:
    if _kind(body) == 'note':
        return 'bad_note'
    if _kind(body) == 'progress' and 'next' in problem:
        return 'bad_next'
    return 'bad_report'


def _bad_message(body: Any, problem: str) -> str:
    if _kind(body) == 'note':
        return f'Marcel did not keep this note: {problem} {NOTE_FALLBACK}'
    return f'Marcel did not take this report. Check it: {problem}'


router = APIRouter(prefix='/api')


async def _read_body(request: Request) -> Any:
    raw = b''
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            raise ApiError(
                400,
                'bad_report',
                f'That report is too big (the most is {MAX_BODY // 1024} kB). Keep reports short '
                'and link to the details.',
            )
    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError(
            400, 'bad_report', 'Marcel did not take this report: the body is not JSON.'
        ) from None


@router.post('/session/report')
async def report_progress(request: Request) -> JSONResponse:
    gate: ReportGate = request.app.state.report_gate
    # The token first: without a good one, even a broken body gets the hidden 404.
    task_id = gate.task_for(request.headers.get('authorization'), caller_of(request))
    await gate.take(task_id, await _read_body(request))
    return JSONResponse({'task_id': task_id}, status_code=202)


def install(app: FastAPI, cloud: CloudTasks) -> ReportGate:
    gate = ReportGate(cloud)
    app.state.report_gate = gate
    app.include_router(router)

    @app.middleware('http')
    async def _hide_other_methods(request: Request, call_next: Any) -> Any:  # pyright: ignore[reportUnusedFunction]
        # Any other method on the report address gets the hidden 404, not a 405 that shows it;
        # so does a trailing slash, not a redirect.
        path = request.url.path
        if path.rstrip('/') == REPORT_PATH and (path != REPORT_PATH or request.method != 'POST'):
            return error_response(404, 'not_found', HIDDEN_MESSAGE)
        return await call_next(request)

    return gate
