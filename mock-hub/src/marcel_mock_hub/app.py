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
from .hub import MAIN_CONVERSATION, Hub, Refused
from .people import avatar_etag

Handler = Callable[[Hub, Request], Awaitable[Response]]


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def not_found(what: str) -> JSONResponse:
    return error(404, 'not_found', f'There is no {what} with that id.')


def bearer(headers: Any) -> str | None:
    """The device token, or None when there is none."""
    scheme, _, token = headers.get('authorization', '').partition(' ')
    return token.strip() if scheme.lower() == 'bearer' and token.strip() else None


def caller(request: Request) -> dict[str, Any]:
    """The person whose phone sent this request (set by `endpoint`)."""
    return request.state.caller


def my_agent(request: Request) -> str:
    return caller(request)['agent']['id']


# ---- handlers that read or change the hub's state -------------------------------------------


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
    return JSONResponse(hub.people.me(caller(request), request.state.token))


def own_agent(request: Request) -> str | None:
    """The path's agent when it is the caller's; another person's is treated as unknown."""
    agent_id = request.path_params['agent_id']
    return agent_id if agent_id == my_agent(request) else None


async def get_agent(hub: Hub, request: Request) -> Response:
    agent_id = own_agent(request)
    return JSONResponse(hub.people.agent(agent_id)) if agent_id else not_found('agent')


async def update_agent(hub: Hub, request: Request) -> Response:
    agent_id = own_agent(request)
    if agent_id is None:
        return not_found('agent')
    body = json.loads(await request.body())
    return JSONResponse(hub.people.update_agent(agent_id, body))


# An agent without a look plays the bundled giraffe; its sheet is drawn from this look.
DEFAULT_LOOK = {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}


def zoo_look(look: dict[str, Any]) -> dict[str, Any]:
    return {'species': look['animal'], 'color': look['color'], 'personality': look['personality']}


async def get_agent_avatar(hub: Hub, request: Request) -> Response:
    """The Pixel Zoo's sheet for the agent's look, as the real hub draws it: the JSON by default,
    the PNG for `Accept: image/png`. Both carry the ETag, which follows the look; `If-None-Match`
    with it answers 304."""
    agent_id = own_agent(request)
    if agent_id is None:
        return not_found('agent')
    agent = hub.people.agent(agent_id)
    etag = avatar_etag(agent)
    headers = {'ETag': etag} if etag else None
    if etag and request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    image, meta = zoo.sheet(zoo_look(agent.get('look') or DEFAULT_LOOK))
    if 'image/png' in request.headers.get('accept', ''):
        return Response(image, media_type='image/png', headers=headers)
    return JSONResponse(meta, headers=headers)


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


def own_conversation(hub: Hub, request: Request) -> str | None:
    conversation_id = request.path_params['conversation_id']
    conversation = hub.conversations.get(conversation_id)
    return (
        conversation_id if conversation and conversation['agent_id'] == my_agent(request) else None
    )


async def list_conversations(hub: Hub, request: Request) -> Response:
    mine = [c for c in hub.conversations.values() if c['agent_id'] == my_agent(request)]
    return JSONResponse(matching(mine, request, 'agent_id', 'kind'))


async def list_messages(hub: Hub, request: Request) -> Response:
    conversation_id = own_conversation(hub, request)
    if conversation_id is None:
        return not_found('conversation')
    items = [m for m in hub.messages if m['conversation_id'] == conversation_id]
    return JSONResponse({'items': paged(items, request)})


async def send_message(hub: Hub, request: Request) -> Response:
    conversation_id = own_conversation(hub, request)
    if conversation_id is None:
        return not_found('conversation')
    body = json.loads(await request.body())
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
    return JSONResponse(hub.search_mentions(my_agent(request), q, kind))


async def list_tasks(hub: Hub, request: Request) -> Response:
    tasks = matching(
        hub.owned('task', my_agent(request)), request, 'agent_id', 'project_id', 'state'
    )
    return JSONResponse({'items': paged(tasks, request)})


async def get_task(hub: Hub, request: Request) -> Response:
    task = hub.tasks.get(request.path_params['task_id'])
    return (
        JSONResponse(task) if task and task['agent_id'] == my_agent(request) else not_found('task')
    )


def own_task(hub: Hub, request: Request) -> dict[str, Any] | None:
    """The path's task when it is the caller's; another person's is treated as unknown."""
    task = hub.tasks.get(request.path_params['task_id'])
    return task if task and task['agent_id'] == my_agent(request) else None


def own_artifact(hub: Hub, request: Request) -> dict[str, Any] | None:
    artifact = hub.artifacts.get(request.path_params['artifact_id'])
    if artifact and hub.project_agent(artifact.get('project_id')) == my_agent(request):
        return artifact
    return None


def task_action(status: int) -> Handler:
    """Steer, stop, archive and hand back answer with the task as it stands: the mock does not
    act on them."""

    async def handler(hub: Hub, request: Request) -> Response:
        task = own_task(hub, request)
        return JSONResponse(task, status_code=status) if task else not_found('task')

    return handler


async def list_task_events(hub: Hub, request: Request) -> Response:
    task_id = request.path_params['task_id']
    if own_task(hub, request) is None:
        return not_found('task')
    after = query_int(request, 'after', 0, 0) or 0
    limit = limit_of(request)
    later = [e for e in hub.task_events.get(task_id, []) if e['seq'] > after]
    return JSONResponse({'items': later[:limit], 'has_more': len(later) > limit})


async def list_approvals(hub: Hub, request: Request) -> Response:
    mine = {t['id'] for t in hub.owned('task', my_agent(request))}
    return JSONResponse(
        [a for a in hub.approvals.values() if a['state'] == 'open' and a['task_id'] in mine]
    )


async def answer_approval(hub: Hub, request: Request) -> Response:
    """Only the caller's own cards; a self-change card (B-24) is the owner's alone."""
    approval = hub.approvals.get(request.path_params['approval_id'])
    if approval is None:
        return not_found('approval')
    owner = caller(request)['role'] == 'owner'
    self_change = approval['tool'] == 'self_change'
    # Someone else's card is unknown, except that the owner answers every self-change card.
    if hub.task_agent(approval['task_id']) != my_agent(request) and not (owner and self_change):
        return not_found('approval')
    if self_change and not owner:
        raise hub.people.owner_only(
            'Only {owner} can approve a change to Marcel itself. Ask {owner} to look at it.'
        )
    body = json.loads(await request.body())
    result = hub.answer(approval['id'], body['decision'])
    assert result is not None
    return JSONResponse(result[1], status_code=result[0])


async def move_task(hub: Hub, request: Request) -> Response:
    """Only the caller's own task, into one of the caller's own projects."""
    task = own_task(hub, request)
    if task is None:
        return not_found('task')
    body = json.loads(await request.body())
    if hub.project_agent(body['project_id']) not in (None, my_agent(request)):
        return not_found('project')
    return JSONResponse(hub.move_task(task, body['project_id']))


async def list_artifacts(hub: Hub, request: Request) -> Response:
    found = matching(
        hub.owned('artifact', my_agent(request)), request, 'task_id', 'project_id', 'kind'
    )
    found.sort(key=lambda a: a['updated_at'], reverse=True)
    return JSONResponse({'items': paged(found, request)})


async def get_artifact(hub: Hub, request: Request) -> Response:
    artifact = hub.artifacts.get(request.path_params['artifact_id'])
    if artifact is None or hub.project_agent(artifact.get('project_id')) != my_agent(request):
        return not_found('artifact')
    return JSONResponse(artifact)


async def get_artifact_content(hub: Hub, request: Request) -> Response:
    artifact = own_artifact(hub, request)
    version = query_int(request, 'version', None, 1)
    body = hub.content(artifact, version) if artifact else None
    if artifact is None or body is None:
        return not_found('artifact version with content')
    return Response(body, media_type=artifact['content_type'])


async def list_artifact_versions(hub: Hub, request: Request) -> Response:
    artifact = own_artifact(hub, request)
    if artifact is None:
        return not_found('artifact')
    return JSONResponse(list(reversed(hub.versions[artifact['id']])))


def project_of(hub: Hub, request: Request) -> dict[str, Any] | None:
    project = hub.projects.get(request.path_params['project_id'])
    return project if project and project['agent_id'] == my_agent(request) else None


async def list_projects(hub: Hub, request: Request) -> Response:
    """The caller's own; another person's agent gives an empty list."""
    archived = request.query_params.get('archived', 'false').lower() == 'true'
    agent_id = request.query_params.get('agent_id', my_agent(request))
    if agent_id != my_agent(request):
        return JSONResponse([])
    return JSONResponse(hub.live_projects(agent_id, archived))


async def create_project(hub: Hub, request: Request) -> Response:
    """Only under the caller's own agent; another person's is treated as unknown."""
    body = json.loads(await request.body())
    if body['agent_id'] != my_agent(request):
        return not_found('agent')
    return JSONResponse(hub.create_project(body), status_code=201)


async def get_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    return JSONResponse(project) if project else not_found('project')


async def update_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    if project is None:
        return not_found('project')
    body = json.loads(await request.body())
    return JSONResponse(hub.update_project(project, body))


async def archive_project(hub: Hub, request: Request) -> Response:
    project = project_of(hub, request)
    if project is None:
        return not_found('project')
    return JSONResponse(hub.archive_project(project))


async def pair(hub: Hub, request: Request) -> Response:
    body = json.loads(await request.body())
    return JSONResponse(hub.people.pair(body['code']), status_code=201)


async def list_users(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.people.list_users())


async def add_user(hub: Hub, request: Request) -> Response:
    body = json.loads(await request.body())
    return JSONResponse(hub.people.add_user(body), status_code=201)


async def remove_user(hub: Hub, request: Request) -> Response:
    hub.people.remove_user(request.path_params['user_id'])
    return Response(status_code=204)


async def create_pairing_code(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.people.issue_code(request.path_params['user_id']), status_code=201)


async def get_health(hub: Hub, request: Request) -> Response:
    return JSONResponse(contract.example('Health'))


HANDLERS: dict[str, Handler] = {
    'getHealth': get_health,
    'pairDevice': pair,
    'getMe': get_me,
    'getAgent': get_agent,
    'updateAgent': update_agent,
    'getAgentAvatar': get_agent_avatar,
    'getAvatarPreview': get_avatar_preview,
    'listUsers': list_users,
    'addUser': add_user,
    'removeUser': remove_user,
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
    owner_only = bool(op.get('x-owner-only'))
    body_check = contract.request_pointer(path, method)
    check = contract.validator(body_check) if body_check else None

    async def run(request: Request) -> Response:
        token = bearer(request.headers)
        person = hub.people.caller(token) if token else None
        if needs_token and person is None:
            return error(
                401, 'unauthorized', 'Pair this phone again: the hub needs a device token.'
            )
        request.state.token, request.state.caller = token, person
        if owner_only and person is not None and person['role'] != 'owner':
            refused = hub.people.owner_only()
            return error(refused.status, refused.code, refused.message)
        if check is not None:
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
        if (
            not token
            or hub.people.caller(token) is None
            or (since is not None and not since.isdigit())
        ):
            await ws.close(code=1008)
            return
        person = hub.people.caller(token)
        assert person is not None
        await ws.accept()
        queue, frames = hub.subscribe(
            int(since) if since is not None else None, person['agent']['id']
        )
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
