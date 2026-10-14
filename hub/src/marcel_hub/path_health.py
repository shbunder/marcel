"""`GET /internal/health`: is the whole path up — the hub, the runner, and a brain that answers?

`/api/health` says the hub is up and whether its runner watch can see the runner. That is not
enough for a deploy or a redeploy rollback (F11): a hub can be up while Marcel's brain is dead or
stuck. This route checks each link:

- **hub**: its database answers.
- **runner**: `GET /health` on the runner's socket answers ok within `RUNNER_TIMEOUT_S`;
  `latency_ms` is that round trip, and `claude_version` / `claude_version_expected` are the
  runner's. When it is not ok, `reason` is the runner's own. `state` is `version_drift` when the
  only thing wrong is that the installed claude differs from the pin (the runner refuses new
  sessions until `deploy/install-claude.sh` runs): a separate state, so a redeploy watchdog does
  not take it for a broken merge. Anything else is `down`.
  `login` is the runner's `Health.login` when its state is not `ok` (runner-api 0.1.13, S-04.18):
  shown, never a cause. A lost login leaves `state` as it is, so a redeploy is never rolled back
  over it (rolling back a good merge cannot log anyone in); `claude_updates.py` tells the owner.
- **brain**, per agent: the main conversation has a brain session, its channel socket is connected,
  and that session sent a frame within `BRAIN_SILENT_S`. The channel server inside the brain's
  `claude` process pings the hub every 20 s (channel-protocol.md), so a recent frame proves that
  process and its link to the hub are alive, without spending a model turn. Waking the brain to
  reply would cost a turn each check, and a hub-to-session ping is not in the channel protocol.

**Local only**, like `/brain`: it lives outside `/api`, so the Cloudflare tunnel never forwards it,
and a request carrying the tunnel's `cf-connecting-ip` header is answered 404. It takes the hub
secret in `X-Marcel-Secret` (401 `bad_secret` without it; `deploy/health.sh` reads it from the
hub's data folder), never a device token, and changes nothing. 200 when every link is ok, 503
otherwise, with the same body.
"""

import asyncio
import hmac
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, col, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.brain_api import _hub_secret
from marcel_hub.channel.gateway import Gateway
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent

TUNNEL_HEADER = 'cf-connecting-ip'
SECRET_HEADER = 'x-marcel-secret'
RUNNER_TIMEOUT_S = 5.0
VERSION_FIELDS = ('claude_version', 'claude_version_expected')
DRIFT_REASON = (
    'Claude Code {claude_version} is installed, but Marcel is tested with '
    '{claude_version_expected}. Run deploy/install-claude.sh.'
)
BRAIN_SILENT_S = 45.0  # the channel server pings every 20 s: two missed pings is down

router = APIRouter(prefix='/internal')


def _db(request: Request) -> str:
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return 'ok'
    except Exception:  # health must answer, whatever is wrong
        return 'down'


async def _runner(request: Request) -> dict[str, Any]:
    client = getattr(request.app.state, 'runner_client', None)
    if client is None:
        return {'state': 'down', 'reason': 'The hub has no runner set up.'}
    started = time.monotonic()
    try:
        answer = await asyncio.wait_for(client.health(), RUNNER_TIMEOUT_S)
    except TimeoutError:
        return {'state': 'down', 'reason': f'No answer in {RUNNER_TIMEOUT_S:g} s.'}
    except Exception as exc:  # the runner client says what went wrong in plain words
        return {'state': 'down', 'reason': str(exc) or type(exc).__name__}
    latency = round((time.monotonic() - started) * 1000)
    if not isinstance(answer, dict):
        return {'state': 'down', 'reason': 'The runner says it is not ok.', 'latency_ms': latency}
    versions = {k: answer[k] for k in VERSION_FIELDS if isinstance(answer.get(k), str)}
    login = answer.get('login')
    if isinstance(login, dict) and login.get('state') != 'ok':  # shown, never a cause
        versions['login'] = login
    if answer.get('ok'):
        return {'state': 'ok', 'latency_ms': latency, **versions}
    state = 'version_drift' if _drift(answer) else 'down'
    reason = answer.get('reason') or (
        DRIFT_REASON.format(**versions)
        if state == 'version_drift'
        else 'The runner says it is not ok.'
    )
    return {'state': state, 'reason': str(reason), 'latency_ms': latency, **versions}


def _drift(answer: dict[str, Any]) -> bool:
    """The runner is up and well except for the claude version: installed is not the pin
    (runner-api.yaml `Health.claude_version_expected`, 0.1.10). Its supervisor runs on its own, so
    only installing the pinned claude is needed, not a rollback."""
    installed = str(answer.get('claude_version') or '').split()
    expected = str(answer.get('claude_version_expected') or '').strip()
    supervisor = answer.get('supervisor') or {}
    return (
        bool(installed and expected)
        and installed[0] != expected
        and supervisor.get('running') is True
        and supervisor.get('own_unit') is True
    )


def _brains(request: Request) -> list[dict[str, Any]]:
    gateway: Gateway = request.app.state.channel
    now = time.monotonic()
    out = []
    with Session(request.app.state.engine) as s:
        # An archived agent has no brain (S-17.7), so it is not checked.
        for agent in s.exec(select(Agent).where(col(Agent.archived_at).is_(None))).all():
            conv = ensure_main_conversation(s, agent)
            sid = conv.brain_session_id
            brain: dict[str, Any] = {'agent_id': agent.id, 'state': 'down'}
            if sid is None:
                brain['reason'] = 'No brain session yet.'
            elif not gateway.outbox.connected(conversation_target(conv.id)):
                brain['reason'] = 'The brain is not connected to the hub.'
            else:
                seen = gateway.last_frame.get(sid)
                if seen is None or now - seen > BRAIN_SILENT_S:
                    brain['reason'] = f'The brain has sent nothing for over {BRAIN_SILENT_S:.0f} s.'
                else:
                    brain = {'agent_id': agent.id, 'state': 'ok', 'last_seen_s': round(now - seen)}
            out.append(brain)
    return out


@router.get('/health')
async def whole_path(request: Request) -> JSONResponse:
    if TUNNEL_HEADER in request.headers:
        raise ApiError(404, 'not_found', 'Not found.')
    gateway: Gateway = request.app.state.channel
    secret = _hub_secret(gateway)  # None when missing or shorter than the channel's minimum
    given = request.headers.get(SECRET_HEADER, '')
    if secret is None or not hmac.compare_digest(secret.encode(), given.encode()):
        raise ApiError(401, 'bad_secret', 'Send the hub secret in X-Marcel-Secret.')
    hub = _db(request)
    runner = await _runner(request)
    brains = _brains(request)
    ok = hub == 'ok' and runner['state'] == 'ok' and all(b['state'] == 'ok' for b in brains)
    body: dict[str, Any] = {'ok': ok, 'hub': hub, 'runner': runner, 'brains': brains}
    updates = getattr(request.app.state, 'claude_updates', None)
    change = updates.open_change() if updates is not None else None
    if change is not None:  # Claude Code was updated under Marcel and is not back on the pin yet
        body['claude_update'] = change
    if not brains:  # a fresh install: nothing to answer yet, so not a failure (install.sh)
        body['note'] = 'No agent yet: create one (docs/operating.md, Install).'
    return JSONResponse(body, status_code=200 if ok else 503)
