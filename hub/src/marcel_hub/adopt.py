"""`POST /api/session/adopt` (contracts/session-api.yaml, B-14): the owner hands a Claude Code
session Marcel did not start to Marcel with `/marcel-adopt` (plugins/marcel/commands/).

- **The adopt token.** One per hub, made with `marcel-hub adopt-token`, which prints it once. The
  hub keeps only its sha256, in `<data_dir>/adopt-token.sha256`, and reads that file on every call,
  so a new token works (and the old one stops) without a restart. Every machine the owner uses
  Claude Code on keeps the token in `~/.marcel/adopt-token`.
- **Hidden.** A call without the right token gets exactly the hub's own 404 for an unknown
  address, so a scanner cannot tell the route exists. After `MAX_WRONG_TOKENS` wrong tokens in a
  minute every call is refused with 429 until the minute is over, the right token too.
- **Once per session.** The same session handed over twice gives the same task: 201 the first
  time, 200 after (also for a session Marcel started itself).
- **What it does.** A task with origin `adopted`, already `working`; an "adopted" milestone in the
  main conversation; and, when the runner on the NUC sees the session, its transcript is followed
  from now on (`tracked: live`). Anything else, another machine or the cloud, is `links_only`:
  Marcel keeps the task and its link, nothing more.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlmodel import Session, col, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.channel.gateway import Gateway
from marcel_hub.models import Agent, Task, TaskLocation, TaskOrigin, TaskState
from marcel_hub.runner_client import RunnerWatch
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.tasks.registry import TaskRegistry

log = logging.getLogger(__name__)

TOKEN_FILE = 'adopt-token.sha256'
TOKEN_PREFIX = 'mra_'
MAX_BODY = 16 * 1024  # bytes; a real request is a few hundred
MAX_WRONG_TOKENS = 10
WRONG_TOKEN_WINDOW = 60.0  # seconds

LOCAL_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
CLOUD_ID = re.compile(r'^session_[A-Za-z0-9]{8,64}$')
HOSTNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')
CONTROL = re.compile(r'[\x00-\x1f\x7f]')

ADOPTED_NOTE = 'Handed over with /marcel-adopt'


def hidden() -> ApiError:
    """The hub's own answer to an unknown address (auth/errors.py), word for word."""
    return ApiError(404, 'not_found', 'There is nothing at that address.')


# The token -----------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_adopt_token(data_dir: Path) -> str:
    """Make a new adopt token, keep only its hash (owner-only file), and return the token. The
    previous token stops working."""
    token = TOKEN_PREFIX + secrets.token_hex(32)
    path = data_dir / TOKEN_FILE
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(_hash(token) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return token


class TokenCheck:
    """Lets the adopt token in; counts wrong ones across all callers (behind the tunnel they
    share an address)."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self.clock = clock
        self.failures: list[float] = []
        self._warned = False

    def _recent(self) -> list[float]:
        now = self.clock()
        self.failures = [t for t in self.failures if now - t < WRONG_TOKEN_WINDOW]
        return self.failures

    def _expected(self) -> str | None:
        try:
            value = self.path.read_text().strip()
        except FileNotFoundError:
            if not self._warned:
                self._warned = True
                log.warning(
                    'Someone ran /marcel-adopt, but the hub has no adopt token yet. '
                    'Make one with `marcel-hub adopt-token`.'
                )
            return None
        return value or None

    def check(self, authorization: str | None) -> None:
        """Raise 429 while too many wrong tokens came in, else 404 unless the token is right."""
        if len(self._recent()) >= MAX_WRONG_TOKENS:
            raise ApiError(
                429,
                'too_many_tries',
                'Too many wrong adopt tokens. Wait a minute, then try again.',
                headers={'Retry-After': str(int(WRONG_TOKEN_WINDOW))},
            )
        scheme, _, token = (authorization or '').partition(' ')
        expected = self._expected()
        given = _hash(token.strip()) if scheme.lower() == 'bearer' and token.strip() else ''
        if expected is None or not given or not hmac.compare_digest(given, expected):
            self.failures.append(self.clock())
            raise hidden()


# The request ---------------------------------------------------------------------------------


class AdoptRequest(BaseModel):
    """`AdoptRequest` in session-api.yaml, with the checks the contract leaves to the hub."""

    model_config = ConfigDict(extra='forbid')

    session_id: str = Field(max_length=80)
    kind: Literal['local', 'cloud']
    title: str = Field(min_length=1, max_length=120)
    cwd: str | None = Field(default=None, max_length=4096)
    machine: str | None = Field(default=None, max_length=253)
    url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode='after')
    def _fits(self) -> Self:
        self.title = self.title.strip()
        if not self.title or CONTROL.search(self.title):
            raise ValueError('the title must be one line of text')
        if self.kind == 'local':
            if not LOCAL_ID.match(self.session_id):
                raise ValueError(
                    'a local session id looks like 6f1c2a9e-8b7d-4c3a-9e2f-0a1b2c3d4e5f'
                )
            if not self.cwd:
                raise ValueError('a local session needs `cwd`, the folder it runs in')
        elif not CLOUD_ID.match(self.session_id):
            raise ValueError('a cloud session id looks like session_0168oBNCs8Q2BrHDFu6tYUdh')
        if self.cwd is not None and (not self.cwd.startswith('/') or CONTROL.search(self.cwd)):
            raise ValueError('`cwd` must be a full path that starts with /')
        if self.machine is not None and not HOSTNAME.match(self.machine):
            raise ValueError('`machine` must be a host name')
        if self.url is not None and (
            not self.url.startswith('https://') or CONTROL.search(self.url) or ' ' in self.url
        ):
            raise ValueError('`url` must be an https:// address')
        return self


def parse(raw: bytes) -> AdoptRequest:
    try:
        return AdoptRequest.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        if first['type'] == 'value_error':  # one of `_fits`' own sentences
            reason = str(first.get('ctx', {}).get('error', first['msg']))
            raise ApiError(400, 'bad_request', f'Check the request: {reason}.') from None
        raise RequestValidationError(exc.errors()) from None


# Adopting ------------------------------------------------------------------------------------


class Adopter:
    def __init__(
        self,
        engine: Any,
        registry: TaskRegistry,
        gateway: Gateway,
        watch: RunnerWatch | None,
        tokens: TokenCheck,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.gateway = gateway
        self.watch = watch
        self.tokens = tokens

    async def adopt(self, req: AdoptRequest) -> tuple[dict[str, str], bool]:
        """The contract's `Adopted`, and whether the task is new."""
        # Two calls for one session make one task: nothing is awaited between looking for the
        # session's task and storing a new one, so on the hub's one event loop no other call can
        # come in between (test_two_calls_at_once_for_one_session_make_one_task).
        existing = self._task_with(req.session_id)
        if existing is not None:
            live = await self._follow_if_seen(existing)
            return self._answer(existing, live), False
        task = self._create(req)
        await self.registry.transition(
            task.id, TaskState.working, 'The session was already running'
        )
        self.gateway.milestone(task.id, 'adopted')
        live = await self._follow_if_seen(task)
        log.info(
            'Adopted session %s as task %s (%s).',
            req.session_id,
            task.id,
            'live' if live else 'links only',
        )
        return self._answer(task, live), True

    def _task_with(self, session_id: str) -> Task | None:
        with Session(self.engine) as s:
            return s.exec(select(Task).where(Task.session_id == session_id)).first()

    def _agent(self) -> Agent:
        """The owner's first agent: the one whose main conversation shows the milestone."""
        with Session(self.engine) as s:
            agent = s.exec(
                select(Agent)
                .where(col(Agent.main_conversation_id).is_not(None))
                .order_by(col(Agent.id))
            ).first()
        if agent is None:
            raise ApiError(
                400,
                'no_agent',
                'Marcel is not set up on this hub yet, so it cannot adopt sessions.',
            )
        return agent

    def _create(self, req: AdoptRequest) -> Task:
        local = req.kind == 'local'
        url = req.url or (None if local else f'https://claude.ai/code/{req.session_id}')
        where = f' from {req.machine or "a computer"} in {req.cwd}' if local else ' from the cloud'
        task = Task(
            agent_id=self._agent().id,
            title=req.title,
            location=TaskLocation.nuc if local else TaskLocation.cloud,
            state=TaskState.starting,
            model='unknown',  # the session chose it; the request does not say
            session_id=req.session_id,
            origin=TaskOrigin.adopted,
            cloud_url=None if local else url,
            remote_url=url if local else None,
        )
        return self.registry.create(
            task, f'{ADOPTED_NOTE}{where}.', TaskState.starting, ADOPTED_NOTE
        )

    async def _follow_if_seen(self, task: Task) -> bool:
        """True when the runner on the NUC lists the session; its transcript is then followed."""
        if self.watch is None or task.location != TaskLocation.nuc or task.session_id is None:
            return False
        try:
            sessions = await self.watch.client.sessions()
        except RunnerUnavailable as exc:
            log.info('Could not ask the runner about session %s: %s', task.session_id, exc)
            return False
        if not any(s.get('id') == task.session_id for s in sessions):
            return False
        self.watch.follow(task.id, task.session_id)
        return True

    @staticmethod
    def _answer(task: Task, live: bool) -> dict[str, str]:
        return {'task_id': task.id, 'tracked': 'live' if live else 'links_only'}


router = APIRouter(prefix='/api')


async def _read_body(request: Request) -> bytes:
    body = b''
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise ApiError(400, 'bad_request', 'That request is too big for an adopt call.')
    return body


@router.post('/session/adopt')
async def adopt_session(request: Request) -> JSONResponse:
    adopter: Adopter = request.app.state.adopter
    # The token first: without it, even a broken body gets the hidden 404.
    adopter.tokens.check(request.headers.get('authorization'))
    adopted, created = await adopter.adopt(parse(await _read_body(request)))
    return JSONResponse(adopted, status_code=201 if created else 200)


def install(
    app: FastAPI, registry: TaskRegistry, gateway: Gateway, watch: RunnerWatch | None
) -> Adopter:
    adopter = Adopter(
        app.state.engine,
        registry,
        gateway,
        watch,
        TokenCheck(app.state.settings.data_dir / TOKEN_FILE),
    )
    app.state.adopter = adopter
    app.include_router(router)
    return adopter


# `marcel-hub adopt-token` ----------------------------------------------------------------------


def token_cli(argv: list[str]) -> int:
    """Make a new adopt token and print it once, with where to put it."""
    from marcel_hub.app import SettingsError, load_settings

    if argv:
        print('Usage: marcel-hub adopt-token', file=sys.stderr)
        return 2
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'No adopt token was made. {exc}') from exc
    token = new_adopt_token(settings.data_dir)
    print('A new adopt token. It is shown only this once; the previous one no longer works.')
    print()
    print(f'    {token}')
    print()
    print('On every computer where you use Claude Code, save it for /marcel-adopt:')
    print('    mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)')
    print('then paste the token, press Enter and then Ctrl-D.')
    return 0
