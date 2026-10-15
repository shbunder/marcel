"""The FastAPI app: one route per operation in `contracts/app-api.yaml`, under `/api`."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import zoo
from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from jsonschema import ValidationError
from zoo import png

from . import contract
from .buddies import avatar_etag
from .hub import MAIN_CONVERSATION, Hub, Refused

Handler = Callable[[Hub, Request], Awaitable[Response]]


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def not_found(what: str) -> JSONResponse:
    return error(404, 'not_found', f'There is no {what} with that id.')


def bearer(headers: Any) -> str | None:
    """The device token, or None when there is none."""
    scheme, _, token = headers.get('authorization', '').partition(' ')
    return token.strip() if scheme.lower() == 'bearer' and token.strip() else None


def token_of(request: Request) -> str | None:
    """The device token of this request (set by `endpoint`)."""
    return request.state.token


async def body_of(request: Request) -> dict[str, Any]:
    raw = await request.body()
    return json.loads(raw) if raw else {}


# ---- handlers that read or change the hub's state -------------------------------------------
# One person sees every buddy (ADR-013): nothing here is scoped to the phone that asks. Lists
# filter by the query parameters the contract names.


def query_int(
    request: Request, name: str, default: int | None, minimum: int, maximum: int | None = None
) -> int | None:
    """A whole-number query parameter. Anything else is a 400, never a 500."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value < minimum or (maximum is not None and value > maximum):
        allowed = f'from {minimum} to {maximum}' if maximum is not None else f'{minimum} or more'
        raise Refused(400, 'bad_request', f'`{name}` must be a whole number, {allowed}.')
    return value


def limit_of(request: Request) -> int:
    return query_int(request, 'limit', 50, 1, 200) or 50


def paged(items: list[dict[str, Any]], request: Request) -> list[dict[str, Any]]:
    return items[: limit_of(request)]


def matching(items: Any, request: Request, *fields: str) -> list[dict[str, Any]]:
    """Keep the items whose fields equal the query parameters of the same name, when given."""
    wanted = {f: request.query_params[f] for f in fields if f in request.query_params}
    return [i for i in items if all(i.get(f) == v for f, v in wanted.items())]


async def get_me(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.buddies.me(token_of(request)))


async def create_agent(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.buddies.create(await body_of(request)), status_code=201)


async def get_agent(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.buddies.agent(request.path_params['agent_id']))


async def update_agent(hub: Hub, request: Request) -> Response:
    body = await body_of(request)
    return JSONResponse(hub.buddies.update(request.path_params['agent_id'], body))


async def archive_agent(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.buddies.archive(request.path_params['agent_id']))


# A buddy without a look plays the bundled giraffe; its sheet is drawn from this look.
DEFAULT_LOOK = {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}


def zoo_look(look: dict[str, Any]) -> dict[str, Any]:
    return {'species': look['animal'], 'color': look['color'], 'personality': look['personality']}


async def get_agent_avatar(hub: Hub, request: Request) -> Response:
    """The Pixel Zoo's sheet for the buddy's look, as the real hub draws it: the JSON by default,
    the PNG for `Accept: image/png`. Both carry the ETag, which follows the look; `If-None-Match`
    with it answers 304."""
    agent = hub.buddies.agent(request.path_params['agent_id'])
    etag = avatar_etag(agent)
    headers = {'ETag': etag} if etag else None
    if etag and request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    image, meta = zoo.sheet(zoo_look(agent.get('look') or DEFAULT_LOOK))
    if 'image/png' in request.headers.get('accept', ''):
        return Response(image, media_type='image/png', headers=headers)
    return JSONResponse(meta, headers=headers)


async def list_devices(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.buddies.list_devices(token_of(request)))


async def update_device(hub: Hub, request: Request) -> Response:
    """Choose the buddy a phone shows (B-49); any phone may change any phone's."""
    body = await body_of(request)
    device_id = request.path_params['device_id']
    return JSONResponse(hub.buddies.show(token_of(request), device_id, body['agent_id']))


async def set_device_push(hub: Hub, request: Request) -> Response:
    body = await body_of(request)
    device_id = request.path_params['device_id']
    return JSONResponse(hub.buddies.set_push(token_of(request), device_id, body))


async def revoke_device(hub: Hub, request: Request) -> Response:
    """A phone the mock paired is forgotten for real ("Sign out of this phone"); any other id
    answers 204 as before."""
    hub.buddies.revoke(request.path_params['device_id'])
    return Response(status_code=204)


async def get_avatar_preview(hub: Hub, request: Request) -> Response:
    """One 32 x 32 PNG of any look, for the look picker. The contract's lists only: anything else
    is a 400."""
    query = request.query_params
    choices = {
        'animal': zoo.SPECIES,
        'color': ('natural', *zoo.COLORS),
        'personality': zoo.PERSONALITIES,
    }
    look: dict[str, str] = {}
    for name, known in choices.items():
        value = query.get(name)
        if value not in known:
            return error(400, 'bad_request', f'Pick a known {name}.')
        look[name] = value
    mood = query.get('mood')
    if mood is not None and mood not in zoo.MOODS:
        return error(400, 'bad_request', 'Pick a known mood.')
    pixels = zoo.to_rgba(zoo.frame(zoo_look(look), mood))
    return Response(png.encode(zoo.N, zoo.N, pixels), media_type='image/png')


def conversation_of(hub: Hub, request: Request) -> str | None:
    conversation_id = request.path_params['conversation_id']
    return conversation_id if conversation_id in hub.conversations else None


async def list_conversations(hub: Hub, request: Request) -> Response:
    """A buddy's conversations; a buddy that does not exist (or none named) gives an empty list."""
    agent_id = request.query_params.get('agent_id')
    found = [c for c in hub.conversations.values() if c['agent_id'] == agent_id]
    return JSONResponse(matching(found, request, 'kind'))


async def list_messages(hub: Hub, request: Request) -> Response:
    conversation_id = conversation_of(hub, request)
    if conversation_id is None:
        return not_found('conversation')
    items = [m for m in hub.messages if m['conversation_id'] == conversation_id]
    return JSONResponse({'items': paged(items, request)})


async def send_message(hub: Hub, request: Request) -> Response:
    conversation_id = conversation_of(hub, request)
    if conversation_id is None:
        return not_found('conversation')
    body = await body_of(request)
    message = hub.user_message(
        conversation_id,
        body['text'],
        body.get('client_id'),
        body.get('project_id'),
        body.get('mentions'),
    )
    if conversation_id == MAIN_CONVERSATION:
        hub.start_scenario(message)
    return JSONResponse(message, status_code=201)


async def search_mentions(hub: Hub, request: Request) -> Response:
    q = request.query_params.get('q', '')
    kind = request.query_params.get('kind')
    if len(q) > 100:
        raise Refused(400, 'bad_request', '`q` may be at most 100 characters.')
    if kind not in (None, 'task', 'project', 'artifact'):
        raise Refused(400, 'bad_request', '`kind` must be task, project or artifact.')
    return JSONResponse(hub.search_mentions(q, kind))


async def list_tasks(hub: Hub, request: Request) -> Response:
    tasks = matching(hub.owned('task'), request, 'agent_id', 'project_id', 'state')
    return JSONResponse({'items': paged(tasks, request)})


def task_of(hub: Hub, request: Request) -> dict[str, Any] | None:
    return hub.tasks.get(request.path_params['task_id'])


async def get_task(hub: Hub, request: Request) -> Response:
    task = task_of(hub, request)
    return JSONResponse(task) if task else not_found('task')


def artifact_of(hub: Hub, request: Request) -> dict[str, Any] | None:
    return hub.artifacts.get(request.path_params['artifact_id'])


def task_action(status: int) -> Handler:
    """Steer, stop, archive and hand back answer with the task as it stands: the mock does not
    act on them."""

    async def handler(hub: Hub, request: Request) -> Response:
        task = task_of(hub, request)
        return JSONResponse(task, status_code=status) if task else not_found('task')

    return handler


async def list_task_events(hub: Hub, request: Request) -> Response:
    task_id = request.path_params['task_id']
    if task_of(hub, request) is None:
        return not_found('task')
    after = query_int(request, 'after', 0, 0) or 0
    limit = limit_of(request)
    later = [e for e in hub.task_events.get(task_id, []) if e['seq'] > after]
    return JSONResponse({'items': later[:limit], 'has_more': len(later) > limit})


async def list_approvals(hub: Hub, request: Request) -> Response:
    return JSONResponse([a for a in hub.approvals.values() if a['state'] == 'open'])


async def answer_approval(hub: Hub, request: Request) -> Response:
    """Any paired phone answers any card, a self-change card too (ADR-013)."""
    approval = hub.approvals.get(request.path_params['approval_id'])
    if approval is None:
        return not_found('approval')
    body = await body_of(request)
    result = hub.answer(approval['id'], body['decision'])
    assert result is not None
    return JSONResponse(result[1], status_code=result[0])


async def move_task(hub: Hub, request: Request) -> Response:
    """Into another project of the same buddy; a project of another buddy is treated as unknown."""
    task = task_of(hub, request)
    if task is None:
        return not_found('task')
    body = await body_of(request)
    if hub.project_agent(body['project_id']) not in (None, task['agent_id']):
        return not_found('project')
    return JSONResponse(hub.move_task(task, body['project_id']))


async def list_artifacts(hub: Hub, request: Request) -> Response:
    found = matching(hub.owned('artifact'), request, 'task_id', 'project_id', 'kind')
    found.sort(key=lambda a: a['updated_at'], reverse=True)
    return JSONResponse({'items': paged(found, request)})


async def get_artifact(hub: Hub, request: Request) -> Response:
    artifact = artifact_of(hub, request)
    return JSONResponse(artifact) if artifact else not_found('artifact')


async def get_artifact_content(hub: Hub, request: Request) -> Response:
    artifact = artifact_of(hub, request)
    version = query_int(request, 'version', None, 1)
    body = hub.content(artifact, version) if artifact else None
    if artifact is None or body is None:
        return not_found('artifact version with content')
    return Response(body, media_type=artifact['content_type'])


async def list_artifact_versions(hub: Hub, request: Request) -> Response:
    artifact = artifact_of(hub, request)
    if artifact is None:
        return not_found('artifact')
    return JSONResponse(list(reversed(hub.versions[artifact['id']])))


def project_of(hub: Hub, request: Request) -> dict[str, Any] | None:
    return hub.projects.get(request.path_params['project_id'])


async def list_projects(hub: Hub, request: Request) -> Response:
    """A buddy's Map; a buddy that does not exist (or none named) gives an empty list."""
    archived = request.query_params.get('archived', 'false').lower() == 'true'
    agent_id = request.query_params.get('agent_id')
    if agent_id not in hub.buddies.records:
        return JSONResponse([])
    return JSONResponse(hub.live_projects(agent_id, archived))


async def create_project(hub: Hub, request: Request) -> Response:
    """Under a live buddy; any other id is a 404."""
    body = await body_of(request)
    if body['agent_id'] not in {a['id'] for a in hub.buddies.live()}:
        return not_found('agent')
    return JSONResponse(hub.create_project(body), status_code=201)


async def get_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    return JSONResponse(project) if project else not_found('project')


async def update_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    if project is None:
        return not_found('project')
    return JSONResponse(hub.update_project(project, await body_of(request)))


async def archive_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    if project is None:
        return not_found('project')
    return JSONResponse(hub.archive_project(project))


async def pair(hub: Hub, request: Request) -> Response:
    body = await body_of(request)
    return JSONResponse(hub.buddies.pair(body['code'], body['device_name']), status_code=201)


async def create_pairing_code(hub: Hub, request: Request) -> Response:
    """A code for one more phone, starting on the buddy named (else the first live one)."""
    body = await body_of(request)
    return JSONResponse(hub.buddies.issue_code(body.get('agent_id')), status_code=201)


async def get_health(hub: Hub, request: Request) -> Response:
    return JSONResponse(contract.example('Health'))  # os: macos, as on a Mac install (B-51)


HANDLERS: dict[str, Handler] = {
    'getHealth': get_health,
    'pairDevice': pair,
    'getMe': get_me,
    'createAgent': create_agent,
    'getAgent': get_agent,
    'updateAgent': update_agent,
    'archiveAgent': archive_agent,
    'getAgentAvatar': get_agent_avatar,
    'getAvatarPreview': get_avatar_preview,
    'listDevices': list_devices,
    'updateDevice': update_device,
    'setDevicePush': set_device_push,
    'revokeDevice': revoke_device,
    'createPairingCode': create_pairing_code,
    'searchMentions': search_mentions,
    'listConversations': list_conversations,
    'listMessages': list_messages,
    'sendMessage': send_message,
    'listTasks': list_tasks,
    'getTask': get_task,
    'steerTask': task_action(202),
    'stopTask': task_action(200),
    'archiveTask': task_action(200),
    'handBackTask': task_action(202),
    'moveTask': move_task,
    'listTaskEvents': list_task_events,
    'listApprovals': list_approvals,
    'answerApproval': answer_approval,
    'listArtifacts': list_artifacts,
    'getArtifact': get_artifact,
    'getArtifactContent': get_artifact_content,
    'listArtifactVersions': list_artifact_versions,
    'listProjects': list_projects,
    'createProject': create_project,
    'getProject': get_project,
    'updateProject': update_project,
    'archiveProject': archive_project,
}

# Operations with no handler above answer with the contract's example for their success schema.


def _resolve(pointer: str) -> dict[str, Any]:
    node: Any = contract.load_api()
    for token in pointer.split('#/', 1)[1].split('/'):
        node = node[token.replace('~1', '/').replace('~0', '~')]
    return node


def example_for(pointer: str) -> Any:
    """The contract's example for a response schema: a component's, or its list's."""
    schema = _resolve(pointer)
    if '$ref' in schema:
        return contract.example(contract.schema_name(schema['$ref']))
    if schema.get('type') == 'array':
        return [contract.example(contract.schema_name(schema['items']['$ref']))]
    return copy.deepcopy(schema['example'])


def example_handler(path: str, method: str, op: dict[str, Any]) -> Handler:
    status = int(next(code for code in op['responses'] if code.startswith('2')))
    pointer = contract.response_pointer(path, method, status)

    async def handler(hub: Hub, request: Request) -> Response:
        if pointer is None:
            return Response(status_code=status)
        return JSONResponse(example_for(pointer), status_code=status)

    return handler


def endpoint(hub: Hub, method: str, path: str, op: dict[str, Any]) -> Callable[..., Any]:
    handler = HANDLERS.get(op['operationId']) or example_handler(path, method, op)
    needs_token = op.get('security') != []
    body_check = contract.request_pointer(path, method)
    check = contract.validator(body_check) if body_check else None
    body_optional = not op.get('requestBody', {}).get('required', False)

    async def run(request: Request) -> Response:
        token = bearer(request.headers)
        if needs_token and (token is None or not hub.buddies.known(token)):
            return error(
                401, 'unauthorized', 'Pair this phone again: the hub needs a device token.'
            )
        request.state.token = token
        if check is not None and not (body_optional and not await request.body()):
            try:
                check.validate(json.loads(await request.body()))
            except (ValueError, ValidationError) as exc:
                reason = exc.message if isinstance(exc, ValidationError) else 'not JSON'
                return error(400, 'bad_request', f'The request body is not valid: {reason}.')
        try:
            return await handler(hub, request)
        except Refused as refused:
            return error(refused.status, refused.code, refused.message)

    return run


def websocket_route(hub: Hub) -> Callable[[WebSocket], Awaitable[None]]:
    async def stream(ws: WebSocket) -> None:
        since = ws.query_params.get('since')
        token = bearer(ws.headers)
        if not token or not hub.buddies.known(token) or (since is not None and not since.isdigit()):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue, frames = hub.subscribe(int(since) if since is not None else None)
        listen = asyncio.create_task(ws.receive())
        try:
            for frame in frames:
                await ws.send_json(frame)
            while True:
                nxt = asyncio.create_task(queue.get())
                await asyncio.wait({nxt, listen}, return_when=asyncio.FIRST_COMPLETED)
                if nxt.done():
                    await ws.send_json(nxt.result())
                else:
                    nxt.cancel()
                if listen.done():
                    if listen.result()['type'] == 'websocket.disconnect':
                        return
                    listen = asyncio.create_task(ws.receive())
        except WebSocketDisconnect:
            return
        finally:
            listen.cancel()
            hub.unsubscribe(queue)

    return stream


def fastapi_path(path: str) -> str:
    return path.replace('{path}', '{path:path}')


def create_app(step_delay: float | None = None) -> FastAPI:
    """`step_delay` is the pause between scenario steps; the default comes from MOCK_DELAY."""
    if step_delay is None:
        step_delay = float(os.environ.get('MOCK_DELAY', '1.0'))
    hub = Hub(step_delay)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await hub.stop()

    app = FastAPI(title='Marcel mock hub', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.hub = hub
    router = APIRouter(prefix='/api')
    for method, path, op in contract.operations():
        if path == '/ws':
            router.add_api_websocket_route(path, websocket_route(hub))
        else:
            router.add_api_route(
                fastapi_path(path),
                endpoint(hub, method, path, op),
                methods=[method.upper()],
                include_in_schema=False,
            )
    app.include_router(router)
    return app
