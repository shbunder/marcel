"""`GET /internal/health`: is the whole path up — the hub, the runner, and a brain that answers?

`/api/health` says the hub is up and whether its runner watch can see the runner. That is not
enough for a deploy or a redeploy rollback (F11): a hub can be up while Marcel's brain is dead or
stuck. This route checks each link:

- **hub**: its database answers.
- **runner**: `GET /health` on the runner's socket answers ok within `RUNNER_TIMEOUT_S`;
  `latency_ms` is that round trip.
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
from sqlmodel import Session, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.brain_api import _hub_secret
from marcel_hub.channel.gateway import Gateway
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent

TUNNEL_HEADER = 'cf-connecting-ip'
SECRET_HEADER = 'x-marcel-secret'
RUNNER_TIMEOUT_S = 5.0
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
    if not (isinstance(answer, dict) and answer.get('ok')):
        return {'state': 'down', 'reason': 'The runner says it is not ok.', 'latency_ms': latency}
    return {'state': 'ok', 'latency_ms': latency}


def _brains(request: Request) -> list[dict[str, Any]]:
    gateway: Gateway = request.app.state.channel
    now = time.monotonic()
    out = []
    with Session(request.app.state.engine) as s:
        for agent in s.exec(select(Agent)).all():
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
    if not brains:  # a fresh install: nothing to answer yet, so not a failure (install.sh)
        body['note'] = 'No agent yet: create one (docs/operating.md, Install).'
    return JSONResponse(body, status_code=200 if ok else 503)
