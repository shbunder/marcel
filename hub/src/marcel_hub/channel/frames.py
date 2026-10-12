"""Checks for the frames a channel server sends (`contracts/channel.schema.json`, `ToHub`).

The hub has no JSON Schema library at run time, so each frame type is checked field by field
here. A test checks these tables against the contract file, so they cannot drift.
"""

import json
import re
from typing import Any

SESSION_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
REQUEST_ID = re.compile(r'^[a-km-z]{5}$')
REPORT_KINDS = frozenset({'started', 'progress', 'artifact', 'done', 'failed'})
ARTIFACT_KINDS = frozenset({'pr', 'branch', 'doc', 'file', 'dashboard', 'link'})
URL_KINDS = frozenset({'pr', 'branch', 'link'})
PATH_KINDS = frozenset({'doc', 'file', 'dashboard'})

# Every field each `ToHub` frame may carry, and whether it is required. `type` is implied.
FIELDS: dict[str, dict[str, bool]] = {
    'hello': {'session_id': True, 'secret': True, 'plugin_version': True, 'resume_after': False},
    'reply': {'call_id': True, 'text': True, 'in_reply_to': False},
    'report': {'call_id': True, 'kind': True, 'text': False, 'summary': False, 'artifact': False,
               'artifacts': False},
    'permission_request': {'request_id': True, 'tool_name': True, 'description': True,
                           'input_preview': False},
    'ack': {'seq': True},
    'ping': {},
}  # fmt: skip
ARTIFACT_FIELDS: dict[str, bool] = {
    'kind': True, 'title': True, 'url': False, 'path': False, 'artifact_id': False,
    'summary': False,
}  # fmt: skip
STRINGS = frozenset(
    {'session_id', 'secret', 'plugin_version', 'call_id', 'text', 'in_reply_to', 'kind', 'summary',
     'request_id', 'tool_name', 'description', 'input_preview', 'title', 'url', 'path',
     'artifact_id'}
)  # fmt: skip


class BadFrame(ValueError):
    """A frame that does not match the contract. It is logged and dropped."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_fields(what: str, frame: dict[str, Any], fields: dict[str, bool]) -> None:
    missing = [k for k, required in fields.items() if required and k not in frame]
    if missing:
        raise BadFrame(f'a {what} lacks {", ".join(missing)}')
    extra = sorted(set(frame) - set(fields))
    if extra:
        raise BadFrame(f'a {what} has unknown fields {", ".join(extra)}')
    wrong = [k for k, v in frame.items() if k in STRINGS and not isinstance(v, str)]
    if wrong:
        raise BadFrame(f'a {what} has the wrong type for {", ".join(wrong)}')


def check_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise BadFrame('an artifact is not an object')
    _check_fields('artifact', artifact, ARTIFACT_FIELDS)
    kind = artifact['kind']
    if kind not in ARTIFACT_KINDS:
        raise BadFrame(f'unknown artifact kind {kind!r}')
    if kind in URL_KINDS and 'url' not in artifact:
        raise BadFrame(f'a {kind} artifact needs a url')
    if kind in PATH_KINDS and 'path' not in artifact:
        raise BadFrame(f'a {kind} artifact needs a path')
    if len(artifact.get('summary', '')) > 200:
        raise BadFrame('an artifact summary is longer than 200 characters')
    return artifact


def _check_report(frame: dict[str, Any]) -> None:
    kind = frame['kind']
    if kind not in REPORT_KINDS:
        raise BadFrame(f'unknown report kind {kind!r}')
    if kind == 'progress' and 'text' not in frame:
        raise BadFrame('a progress report needs text')
    if kind == 'artifact' and 'artifact' not in frame:
        raise BadFrame('an artifact report needs an artifact')
    if kind in ('done', 'failed') and 'summary' not in frame:
        raise BadFrame(f'a {kind} report needs a summary')
    if 'artifact' in frame:
        check_artifact(frame['artifact'])
    if 'artifacts' in frame:
        if not isinstance(frame['artifacts'], list):
            raise BadFrame('report artifacts is not a list')
        for artifact in frame['artifacts']:
            check_artifact(artifact)


def parse(raw: str) -> dict[str, Any]:
    """Parse and check one `ToHub` frame. Raises `BadFrame`."""
    try:
        frame = json.loads(raw)
    except ValueError:
        raise BadFrame('a frame is not JSON') from None
    if not isinstance(frame, dict):
        raise BadFrame('a frame is not an object')
    kind = frame.get('type')
    if kind not in FIELDS:
        raise BadFrame(f'unknown frame type {kind!r}')
    body = {k: v for k, v in frame.items() if k != 'type'}
    _check_fields(kind, body, FIELDS[kind])
    if kind == 'hello':
        if not SESSION_ID.match(body['session_id']):
            raise BadFrame('a hello has a session id that is not a UUID')
        if 'resume_after' in body and (
            not _is_int(body['resume_after']) or body['resume_after'] < 0
        ):
            raise BadFrame('a hello has a bad resume_after')
    elif kind == 'ack':
        if not _is_int(body['seq']) or body['seq'] < 1:
            raise BadFrame('an ack has a bad seq')
    elif kind == 'permission_request':
        if not REQUEST_ID.match(body['request_id']):
            raise BadFrame('a permission request has a bad request_id')
    elif kind == 'report':
        _check_report(body)
    return frame


def version_tuple(version: str) -> tuple[int, ...] | None:
    """`0.1.0` → (0, 1, 0), `0.2.0-dev` → (0, 2, 0); no leading number → None."""
    match = re.match(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?', version)
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())
