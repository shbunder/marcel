"""Checks for the frames a channel server sends (`contracts/channel.schema.json`, `ToHub`).

The hub has no JSON Schema library at run time, so each frame type is checked field by field
here. A test checks these tables against the contract file, so they cannot drift.

A bad `reply` or `report` that still carries a `call_id` raises `BadCall`: the socket answers it
with `call_ack` + `error`, so the session's tool returns the reason and the call is not re-sent
for ever (channel-protocol.md: every refused call is answered). Any other bad frame is logged
and dropped. A note's text is checked by the gateway (`bad_note`), like a progress report's `next`.
"""

import json
import re
from typing import Any

SESSION_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
REQUEST_ID = re.compile(r'^[a-km-z]{5}$')
REPORT_KINDS = frozenset({'started', 'progress', 'artifact', 'done', 'failed', 'note'})
ARTIFACT_KINDS = frozenset({'pr', 'branch', 'doc', 'file', 'dashboard', 'link'})
URL_KINDS = frozenset({'pr', 'branch', 'link'})
PATH_KINDS = frozenset({'doc', 'file', 'dashboard'})
# A progress report's `next` steps (channel.schema.json `Report.next`).
MAX_NEXT_STEPS = 20
MAX_NEXT_STEP_CHARS = 200
# A `note` report's text: one fact for the project's notes (channel.schema.json `Report`).
MAX_NOTE_CHARS = 1000
NON_SPACE = re.compile(r'\S')  # a note's text needs one character that is not a space
CALLS = frozenset({'reply', 'report'})  # the frames a session waits on a `call_ack` for

# Every field each `ToHub` frame may carry, and whether it is required. `type` is implied.
FIELDS: dict[str, dict[str, bool]] = {
    'hello': {'session_id': True, 'secret': True, 'plugin_version': True, 'resume_after': False},
    'reply': {'call_id': True, 'text': True, 'in_reply_to': False},
    'report': {'call_id': True, 'kind': True, 'text': False, 'next': False, 'summary': False,
               'artifact': False, 'artifacts': False},
    'permission_request': {'request_id': True, 'tool_name': True, 'description': True,
                           'input_preview': False},
    'ack': {'seq': True},
    'ping': {},
}  # fmt: skip
ARTIFACT_FIELDS: dict[str, bool] = {
    'kind': True, 'title': True, 'url': False, 'path': False, 'artifact_id': False,
    'summary': False, 'role': False,
}  # fmt: skip
ARTIFACT_ROLES = frozenset({'tracker'})  # B-46: the project's tracker (`role` in channel 0.7)
# S-20.3 (channel `Artifact.role`, S-02.13): a tracker is a claude.ai artifact, nothing else.
TRACKER_URL = re.compile(r'https://claude\.ai/(code/)?artifact/[A-Za-z0-9_-]+')
STRINGS = frozenset(
    {'session_id', 'secret', 'plugin_version', 'call_id', 'text', 'in_reply_to', 'kind', 'summary',
     'request_id', 'tool_name', 'description', 'input_preview', 'title', 'url', 'path',
     'artifact_id', 'role'}
)  # fmt: skip


class BadFrame(ValueError):
    """A frame that does not match the contract. It is logged and dropped."""


class BadCall(BadFrame):
    """A `reply` or `report` that does not match the contract but names its `call_id`: it is
    answered with `call_ack` + `error` (`ack`), never stored."""

    def __init__(self, call_id: str, kind: str, why: str) -> None:
        super().__init__(why)
        self.call_id = call_id
        self.code = f'bad_{kind}'
        self.message = f'Marcel did not take this {kind}: {why}. Fix it and call again.'

    def ack(self) -> dict[str, Any]:
        return {'type': 'call_ack', 'call_id': self.call_id,
                'error': {'code': self.code, 'message': self.message}}  # fmt: skip


def note_problem(text: Any) -> str | None:
    """Why a `note` report's text breaks the contract, in plain words; None when it is fine."""
    if not isinstance(text, str) or NON_SPACE.search(text) is None:  # the contract's pattern `\S`
        return 'a note needs the fact as text'
    if len(text) > MAX_NOTE_CHARS:
        return f'the note is {len(text)} characters; keep it to {MAX_NOTE_CHARS}'
    return None


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
    if 'role' in artifact:
        if artifact['role'] not in ARTIFACT_ROLES:
            raise BadFrame(f'unknown artifact role {artifact["role"]!r}')
        if kind != 'link' or not TRACKER_URL.fullmatch(artifact['url']):
            raise BadFrame('a tracker is a claude.ai artifact link')
    return artifact


def next_steps_problem(steps: Any) -> str | None:
    """Why a report's `next` breaks the contract, in plain words for the worker; None when it is
    fine. The gateway refuses such a report with an error ack rather than cutting it short."""
    if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
        return 'next must be a list of steps, each one a piece of text'
    if len(steps) > MAX_NEXT_STEPS:
        return f'next has {len(steps)} steps; send at most {MAX_NEXT_STEPS}'
    if any(not step for step in steps):
        return 'a step in next is empty'
    long = [i + 1 for i, step in enumerate(steps) if len(step) > MAX_NEXT_STEP_CHARS]
    if long:
        which = ', '.join(map(str, long))
        one = len(long) == 1
        return (
            f'{"step" if one else "steps"} {which} of next {"is" if one else "are"} longer than '
            f'{MAX_NEXT_STEP_CHARS} characters; shorten {"it" if one else "them"}'
        )
    return None


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
    call_id = body.get('call_id')
    if kind in CALLS and isinstance(call_id, str) and call_id:
        try:
            _check_fields(kind, body, FIELDS[kind])
            if kind == 'report':
                _check_report(body)
        except BadFrame as exc:
            raise BadCall(call_id, kind, str(exc)) from None
        return frame
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
