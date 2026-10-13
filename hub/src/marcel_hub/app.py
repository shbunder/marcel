"""The hub's FastAPI app factory and its settings.

Settings come from a TOML file (`/data/marcel.toml`, or `MARCEL_CONFIG`) and then from the
environment, which wins. A missing file is fine: defaults apply. A broken file stops the hub at
start with a message that names the file.
"""

import functools
import logging
import os
import sys
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from marcel_hub import (
    __version__,
    adopt,
    agent_state,
    artifacts,
    auth,
    boards,
    brain,
    brain_api,
    channel,
    cloud,
    conversations,
    db,
    digest,
    github,
    memory,
    notify,
    projects,
    report_api,
    schedule,
    side,
    task_threads,
    tasks,
    usage,
    ws,
)
from marcel_hub.runner_client import DEFAULT_SOCKET, OffsetStore, RunnerClient, RunnerWatch
from marcel_hub.schedule.cron import zone

DEFAULT_CONFIG_PATH = Path('/data/marcel.toml')

log = logging.getLogger(__name__)


class SettingsError(Exception):
    """The settings cannot be read. The message says which file or variable and what to do."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path('/data')
    db_path: Path = Path('/data/marcel.db')
    host: str = '0.0.0.0'
    port: int = 7420
    owner_name: str = 'Owner'
    # None: no runner (tests). `load_settings` defaults it to the socket mounted into the hub.
    runner_sock: Path | None = None
    worker_cwd: str | None = None
    # None: no brain keeper (tests). The brain workspace on the NUC, e.g. /home/<you>/marcel/brain.
    brain_cwd: str | None = None
    # The brain model's context window in tokens; past 70 % of it the brain rolls over (S-06.7).
    brain_context_window: int = brain.CONTEXT_WINDOW
    # None: no ntfy pushes. e.g. 'https://ntfy.sh' or a self-hosted server (S-08.1).
    ntfy_server: str | None = None
    # None: no APNs pushes (`push.apns` false). The .p8 key and its ids; never shown (S-08.2).
    apns: notify.ApnsConfig | None = field(default=None, repr=False)
    # None: PR cards say the hub cannot ask GitHub. A read-only token (ADR-002); never shown.
    github_token: str | None = field(default=None, repr=False)
    # The owner's time zone: the built-in digests fire in it (S-09.2).
    tz: str = 'Europe/Brussels'


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    config_path = Path(env.get('MARCEL_CONFIG', DEFAULT_CONFIG_PATH))
    values: dict[str, Any] = {}
    if config_path.exists():
        try:
            values = tomllib.loads(config_path.read_text())
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise SettingsError(
                f'Cannot read {config_path}: {exc}. Fix the file or remove it.'
            ) from exc
    values = {
        **values,
        **{k[len('MARCEL_') :].lower(): v for k, v in env.items() if _is_setting(k)},
    }

    known = {
        'data_dir',
        'db_path',
        'host',
        'port',
        'owner_name',
        'runner_sock',
        'worker_cwd',
        'brain_cwd',
        'brain_context_window',
        'ntfy_server',
        'apns_key',
        'apns_key_id',
        'apns_team_id',
        'apns_topic',
        'apns_sandbox',
        'github_token',
        'tz',
    }
    unknown = set(values) - known
    if unknown:
        raise SettingsError(f'Unknown setting(s) {sorted(unknown)} in {config_path} or MARCEL_*.')
    try:
        data_dir = Path(values.get('data_dir', '/data'))
        return Settings(
            data_dir=data_dir,
            db_path=Path(values.get('db_path', data_dir / 'marcel.db')),
            host=str(values.get('host', '0.0.0.0')),
            port=int(values.get('port', 7420)),
            owner_name=str(values.get('owner_name', 'Owner')),
            runner_sock=Path(values.get('runner_sock', DEFAULT_SOCKET)),
            worker_cwd=str(values['worker_cwd']) if values.get('worker_cwd') else None,
            brain_cwd=str(values['brain_cwd']) if values.get('brain_cwd') else None,
            brain_context_window=_context_window(
                values.get('brain_context_window', brain.CONTEXT_WINDOW)
            ),
            ntfy_server=_ntfy_server(values.get('ntfy_server')),
            apns=_apns(values),
            github_token=str(values['github_token']).strip() or None
            if values.get('github_token')
            else None,
            tz=_tz(values.get('tz', 'Europe/Brussels')),
        )
    except ValueError as exc:
        raise SettingsError(f'The port must be a number: {exc}.') from exc
    except TypeError as exc:
        raise SettingsError(
            f'A setting in {config_path} has the wrong type: {exc}. Use text for paths.'
        ) from exc


def _ntfy_server(value: Any) -> str | None:
    if not value:
        return None
    server = str(value).rstrip('/')
    if not server.startswith(('https://', 'http://')):
        raise SettingsError(
            f'ntfy_server must be a web address such as https://ntfy.sh, not {server!r}.'
        )
    return server


def _apns(values: dict[str, Any]) -> notify.ApnsConfig | None:
    try:
        return notify.ApnsConfig.from_values(values)
    except notify.ApnsSettingsError as exc:
        raise SettingsError(str(exc)) from None


def _tz(value: Any) -> str:
    try:
        zone(str(value))
    except schedule.CronError as exc:
        raise SettingsError(f'tz: {exc}') from exc
    return str(value)


def _context_window(value: Any) -> int:
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        tokens = 0
    if tokens < 10_000:
        raise SettingsError(
            f'brain_context_window must be a number of tokens, at least 10000 (got {value!r}). '
            'Leave it out for 200000, the Opus window.'
        )
    return tokens


def _is_setting(name: str) -> bool:
    return name in {
        'MARCEL_DATA_DIR',
        'MARCEL_DB_PATH',
        'MARCEL_HOST',
        'MARCEL_PORT',
        'MARCEL_OWNER_NAME',
        'MARCEL_RUNNER_SOCK',
        'MARCEL_WORKER_CWD',
        'MARCEL_BRAIN_CWD',
        'MARCEL_BRAIN_CONTEXT_WINDOW',
        'MARCEL_NTFY_SERVER',
        'MARCEL_APNS_KEY',
        'MARCEL_APNS_KEY_ID',
        'MARCEL_APNS_TEAM_ID',
        'MARCEL_APNS_TOPIC',
        'MARCEL_APNS_SANDBOX',
        'MARCEL_GITHUB_TOKEN',
        'MARCEL_TZ',
    }


router = APIRouter(prefix='/api')


@router.get('/health')
def health(request: Request) -> dict[str, Any]:
    engine: Engine = request.app.state.engine
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception:  # health must answer, whatever is wrong
        db_ok = False
    watch: RunnerWatch | None = request.app.state.runner_watch
    if watch is None:
        runner = {
            'reachable': False,
            'reason': 'The hub has no runner set up, so it cannot see the NUC.',
        }
    elif watch.reachable:
        runner = {'reachable': True}
    else:
        runner = {'reachable': False, 'reason': watch.reason}
    return {'ok': db_ok, 'version': __version__, 'runner': runner}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = db.make_engine(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.upgrade(engine)
        app.state.channel.recheck()  # approval cards left open while the hub was down
        pusher.start()
        await app.state.tasks.drain()  # tasks queued before a restart start if there is room
        if watch is not None:
            watch.start()
            guard.start()  # S-10.1: reads plan usage from the runner
        keeper.mark_all()  # every board is rebuilt once at start (S-04.11)
        keeper.start()
        avatars.prime()  # states that changed while the hub was down (S-04.8)
        avatars.start()
        prs.start()  # S-04.12: open PRs found again, then refreshed every 5 minutes
        digests.ensure_builtins()  # S-09.2: before the scheduler's first look
        digests.start()
        scheduler.start()  # S-09.1: missed fires are reported once, on its first look
        if keeper_of_brains is not None and keeper_of_brains.runner is not None:
            keeper_of_brains.start()  # S-06.2: one brain per agent, from the first second
        sides.start()  # S-06.3: does nothing until the hub has a runner
        clouds.start_watchdog()  # S-07.1: overdue cloud tasks go silent
        yield
        await clouds.stop_watchdog()
        await sides.stop()
        if keeper_of_brains is not None:
            await keeper_of_brains.stop()
        await scheduler.stop()  # a look in progress finishes first
        await digests.stop()
        await prs.stop()
        await avatars.stop()
        await keeper.stop()
        await guard.stop()
        if watch is not None:
            await watch.close()
            await watch.client.aclose()
        await pusher.aclose()
        engine.dispose()

    # No /docs, /redoc or /openapi.json: every route but /health and /pair needs a token.
    app = FastAPI(
        title='Marcel hub',
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.include_router(router)
    auth.install(app)
    # The runner client (S-04.5) replaces the registry's NoRunner when a socket is configured.
    client = (
        RunnerClient(settings.runner_sock, worker_cwd=settings.worker_cwd)
        if settings.runner_sock
        else None
    )
    # One event log for the app's live stream (S-04.3); the task registry publishes into it.
    app.state.events = ws.EventLog(engine)
    # Pushes (S-08.1) and the channel gateway (S-04.6) both watch the registry's events on their way
    # to the one event log: the pusher pushes on state changes; the gateway expires or settles
    # approval cards. The gateway is also the registry's Outbox.
    pusher = notify.install(app, settings.ntfy_server, settings.apns)
    gateway = channel.create(app, settings.data_dir, push=pusher, alerts=pusher)
    # S-10.1: the usage guard sees limit hits in the registry's events once they are stored.
    guard = usage.create(
        client,
        settings.data_dir,
        pusher,
        ws.OwnerPublisher(app.state.events),
        zone(settings.tz),  # the owner's time zone, never the process's
    )
    registry = tasks.install(
        app,
        runner=client,
        outbox=gateway.outbox,
        # Outermost the pusher: it pushes after the event is stored and the gateway has settled
        # any approval card the change affects.
        publisher=pusher.task_publisher(
            gateway.task_publisher(guard.task_publisher(ws.OwnerPublisher(app.state.events)))
        ),
    )
    watch = (
        RunnerWatch(
            client,
            registry,
            alerts=pusher,
            offsets=OffsetStore(settings.data_dir / 'runner-offsets.json'),
        )
        if client
        else None
    )
    app.state.runner_watch = watch
    ws.install(app)
    app.include_router(conversations.router)
    artifacts.install(app)  # S-04.7, after tasks: an artifact is recorded on its task
    # S-04.12: PR cards follow GitHub (read-only token); new PR artifacts are seen in the event log.
    prs = github.install(app, settings.github_token)
    repo = memory.install(app)
    channel.install(app, gateway, registry, watch)  # after artifacts: reports store through it
    # S-07.1: cloud tasks open claude.ai/code sessions; their reports and a deadline watchdog.
    clouds = cloud.install(app, registry, gateway, client, settings.data_dir)
    # S-07.2: `POST /api/session/report`, where cloud workers report with their task's token.
    report_api.install(app, clouds)
    # S-05.4: `/marcel-adopt` hands a session Marcel did not start to it, as an adopted task.
    adopt.install(app, registry, gateway, watch)
    # S-04.9: a task's own thread steers the task and shows its later milestones.
    task_threads.install(app)
    # S-04.10: workers start with their project's notes; task changes update their project.
    projects.install(app, ws.OwnerPublisher(app.state.events), registry, repo)
    # S-04.11: every project's board follows its tasks, approvals and items.
    keeper = boards.install(app)
    # S-04.8: what each avatar shows, from its tasks and its brain's turns, sent on change.
    avatars = agent_state.install(app)
    # S-05.3: the brain's tools, outside /api (the tunnel never forwards them); last, as they use
    # the gateway, tasks, artifacts, memory and projects.
    brain_api.install(app)
    app.state.brain_ports.boards = boards.BrainBoards(keeper)  # the brain's board_note
    # S-10.1: holds new spawns near the plan's limit; serves /api/usage and the brain's usage_get.
    usage.install(app, guard, registry, watch)
    # S-09.1: schedules fire through the registry (so the guard and the cap apply) or the outbox.
    scheduler = schedule.install(app, registry, gateway.outbox, pusher, settings.data_dir)
    # S-09.2: the digest schedules' events carry the facts bundle; the brain writes the digest.
    digests = digest.install(app, scheduler, pusher, guard, settings.data_dir, settings.tz)
    # S-06.2: keeps one brain session per agent, and rolls it over nightly. It starts the brain
    # through the runner directly, never through the task registry, so the worker cap and the
    # usage guard never hold it back: the brain must always answer.
    keeper_of_brains = (
        brain.BrainKeeper(
            engine,
            client,
            cwd=settings.brain_cwd,
            events=app.state.events,
            alerts=pusher,
            memory=repo,
            tz=zone(settings.tz),  # 04:00 in the owner's time zone, never the process's
            window=settings.brain_context_window,
        )
        if settings.brain_cwd
        else None
    )
    app.state.brain = keeper_of_brains
    # S-06.3: each side thread that is not a task's runs in a fork of the brain, started through
    # the runner directly like the brain; at most three run at once, idle ones stop after 30 min.
    sides = side.install(app, client, gateway.outbox)
    if keeper_of_brains is None:
        log.warning(
            'MARCEL_BRAIN_CWD is not set, so the hub starts no brain and Marcel will not answer '
            'messages. Set it to the brain workspace on the NUC, e.g. /home/<you>/marcel/brain.'
        )
    else:
        gateway.brains = keeper_of_brains  # a new brain's hello is the hand-over
        keeper_of_brains.turn_open = avatars.turn_open  # never hand over mid-turn
        keeper_of_brains.hand_over = gateway.hand_over  # what the seed could not hold
        if watch is not None:
            watch.pokes.append(keeper_of_brains.poke)
        if client is not None:  # S-06.7: how full the brain's context is, from its transcript
            keeper_of_brains.turn_ends = functools.partial(brain.tail_turn_ends, client)
    app.include_router(brain.router)  # S-06.7: manual rollover and the rollover history
    return app


def main(argv: list[str] | None = None) -> None:
    """`marcel-hub`: start the hub on the configured host and port.

    `marcel-hub pair` prints a QR code that pairs a phone instead; `marcel-hub adopt-token` makes
    the token `/marcel-adopt` needs and prints it once.
    """
    import uvicorn

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ['pair']:
        from marcel_hub.auth.cli import pair

        raise SystemExit(pair(args[1:]))
    if args[:1] == ['adopt-token']:
        raise SystemExit(adopt.token_cli(args[1:]))

    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'Marcel hub did not start. {exc}') from exc
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        ws_max_size=channel.MAX_FRAME,  # no socket frame is read past the channel's cap
    )
