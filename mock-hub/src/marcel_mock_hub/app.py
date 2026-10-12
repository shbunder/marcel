"""The FastAPI app: one route per operation in `contracts/app-api.yaml`, under `/api`."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from jsonschema import ValidationError

from . import contract
from .hub import AGENT_ID, MAIN_CONVERSATION, Hub

Handler = Callable[[Hub, Request], Awaitable[Response]]


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def not_found(what: str) -> JSONResponse:
    return error(404, 'not_found', f'There is no {what} with that id.')


def authorized(headers: Any) -> bool:
    scheme, _, token = headers.get('authorization', '').partition(' ')
    return scheme.lower() == 'bearer' and bool(token.strip())


# ---- handlers that read or change the hub's state -------------------------------------------


def paged(items: list[dict[str, Any]], request: Request) -> list[dict[str, Any]]:
    limit = int(request.query_params.get('limit', 50))
    return items[:limit]


async def get_me(hub: Hub, request: Request) -> Response:
    return JSONResponse(hub.me())


async def get_agent(hub: Hub, request: Request) -> Response:
    if request.path_params['agent_id'] != AGENT_ID:
        return not_found('agent')
    return JSONResponse(hub.agent())


async def update_agent(hub: Hub, request: Request) -> Response:
    if request.path_params['agent_id'] != AGENT_ID:
        return not_found('agent')
    body = json.loads(await request.body())
    return JSONResponse({**hub.agent(), **body})


async def list_conversations(hub: Hub, request: Request) -> Response:
    return JSONResponse(list(hub.conversations.values()))


async def list_messages(hub: Hub, request: Request) -> Response:
    conversation_id = request.path_params['conversation_id']
    if conversation_id not in hub.conversations:
        return not_found('conversation')
    items = [m for m in hub.messages if m['conversation_id'] == conversation_id]
    return JSONResponse({'items': paged(items, request)})


async def send_message(hub: Hub, request: Request) -> Response:
    conversation_id = request.path_params['conversation_id']
    if conversation_id not in hub.conversations:
        return not_found('conversation')
    body = json.loads(await request.body())
    message = hub.user_message(conversation_id, body['text'], body.get('client_id'))
    if conversation_id == MAIN_CONVERSATION:
        hub.start_scenario(message)
    return JSONResponse(message, status_code=201)


async def list_tasks(hub: Hub, request: Request) -> Response:
    return JSONResponse({'items': paged(list(hub.tasks.values()), request)})


async def get_task(hub: Hub, request: Request) -> Response:
    task = hub.tasks.get(request.path_params['task_id'])
    return JSONResponse(task) if task else not_found('task')


def task_action(status: int) -> Handler:
    """Steer, stop, archive and hand back answer with the task as it stands: the mock does not
    act on them."""

    async def handler(hub: Hub, request: Request) -> Response:
        task = hub.tasks.get(request.path_params['task_id'])
        return JSONResponse(task, status_code=status) if task else not_found('task')

    return handler


async def list_task_events(hub: Hub, request: Request) -> Response:
    task_id = request.path_params['task_id']
    if task_id not in hub.tasks:
        return not_found('task')
    after = int(request.query_params.get('after', 0))
    limit = int(request.query_params.get('limit', 50))
    later = [e for e in hub.task_events.get(task_id, []) if e['seq'] > after]
    return JSONResponse({'items': later[:limit], 'has_more': len(later) > limit})


async def list_approvals(hub: Hub, request: Request) -> Response:
    return JSONResponse([a for a in hub.approvals.values() if a['state'] == 'open'])


async def answer_approval(hub: Hub, request: Request) -> Response:
    body = json.loads(await request.body())
    result = hub.answer(request.path_params['approval_id'], body['decision'])
    if result is None:
        return not_found('approval')
    return JSONResponse(result[1], status_code=result[0])


async def list_artifacts(hub: Hub, request: Request) -> Response:
    return JSONResponse({'items': paged(list(hub.artifacts.values()), request)})


async def get_artifact(hub: Hub, request: Request) -> Response:
    artifact = hub.artifacts.get(request.path_params['artifact_id'])
    return JSONResponse(artifact) if artifact else not_found('artifact')


async def get_artifact_content(hub: Hub, request: Request) -> Response:
    artifact = hub.artifacts.get(request.path_params['artifact_id'])
    if artifact is None or 'content_type' not in artifact:
        return not_found('artifact with content')
    return Response(
        f'# {artifact["title"]}\n\nThe mock hub has no real content for this document.\n',
        media_type=artifact['content_type'],
    )


async def pair(hub: Hub, request: Request) -> Response:
    return JSONResponse(contract.example('PairResponse'), status_code=201)


async def get_health(hub: Hub, request: Request) -> Response:
    return JSONResponse(contract.example('Health'))


HANDLERS: dict[str, Handler] = {
    'getHealth': get_health,
    'pairDevice': pair,
    'getMe': get_me,
    'getAgent': get_agent,
    'updateAgent': update_agent,
    'listConversations': list_conversations,
    'listMessages': list_messages,
    'sendMessage': send_message,
    'listTasks': list_tasks,
    'getTask': get_task,
    'steerTask': task_action(202),
    'stopTask': task_action(200),
    'archiveTask': task_action(200),
    'handBackTask': task_action(202),
    'listTaskEvents': list_task_events,
    'listApprovals': list_approvals,
    'answerApproval': answer_approval,
    'listArtifacts': list_artifacts,
    'getArtifact': get_artifact,
    'getArtifactContent': get_artifact_content,
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

    async def run(request: Request) -> Response:
        if needs_token and not authorized(request.headers):
            return error(
                401, 'unauthorized', 'Pair this phone again: the hub needs a device token.'
            )
        if check is not None:
            try:
                check.validate(json.loads(await request.body()))
            except (ValueError, ValidationError) as exc:
                reason = exc.message if isinstance(exc, ValidationError) else 'not JSON'
                return error(400, 'bad_request', f'The request body is not valid: {reason}.')
        return await handler(hub, request)

    return run


def websocket_route(hub: Hub) -> Callable[[WebSocket], Awaitable[None]]:
    async def stream(ws: WebSocket) -> None:
        since = ws.query_params.get('since')
        if not authorized(ws.headers) or (since is not None and not since.isdigit()):
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
