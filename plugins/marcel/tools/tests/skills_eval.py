"""The brain-routing eval for the plugin's skills (S-05.5): canned requests → expected tool calls.

`plugins/marcel/skills/evals/brain-routing.json` holds the cases. For each one, the live test
(`test_skills_eval_live.py`, NUC only) starts a real `claude -p` with the `orchestrate` skill as its
system prompt and this file as a fake MCP server (`python skills_eval.py serve <case> <log>`). The
fake server lists the real brain tools plus the channel's `reply`, logs every call and answers it
from the case's canned `answers`, so nothing reaches the hub. `check_case` then compares the log
with the case's `expect` and `forbid`.
"""

import json
import sys
from pathlib import Path
from typing import Any

from marcel_tools.server import Caller, Server
from marcel_tools.tools import TOOLS

ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / 'plugins' / 'marcel' / 'skills'
CASES = SKILLS / 'evals' / 'brain-routing.json'
SERVER_NAME = 'marcel_tools'

# The channel's `reply` tool (plugins/marcel/channel/src/tools.ts), so the brain can answer.
REPLY = {
    'name': 'reply',
    'description': 'Say something to the owner in the Marcel conversation. Short sentences, '
    'outcome first, Markdown allowed.',
    'inputSchema': {
        'type': 'object',
        'properties': {'text': {'type': 'string'}, 'in_reply_to': {'type': 'string'}},
        'required': ['text'],
    },
}
PREAMBLE = (
    'You are Marcel, the brain session. The next user message is the owner writing in the main '
    'chat from their phone. Act on it with your tools, following the orchestrate skill below, '
    'and answer the owner with the reply tool.\n\n'
)


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES.read_text())['cases']


def tool_names() -> set[str]:
    return {tool.name for tool in TOOLS} | {REPLY['name']}


def _matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, str):
            if not isinstance(got, str) or want.lower() not in got.lower():
                return False
        elif got != want:
            return False
    return True


def check_case(case: dict[str, Any], calls: list[dict[str, Any]]) -> list[str]:
    """What went wrong, in plain sentences; empty when the calls meet the case."""
    problems: list[str] = []
    made = [call['tool'] for call in calls]
    at = 0
    for step in case['expect']:
        found = next(
            (i for i in range(at, len(calls))
             if calls[i]['tool'] == step['tool'] and _matches(step['args'], calls[i]['args'])),
            None,
        )  # fmt: skip
        if found is None:
            problems.append(f'{case["id"]}: expected {step["tool"]} {step["args"]}, got {made}.')
            break
        at = found + 1
    for tool in case['forbid']:
        if tool in made:
            problems.append(f'{case["id"]}: called {tool}, which this request must not use.')
    return problems


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class EvalServer(Server):
    """The real MCP server loop, with the channel's `reply` added to the listing."""

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if msg.get('method') == 'tools/list':
            listing = [tool.listing() for tool in TOOLS] + [REPLY]
            self._result(msg.get('id'), {'tools': listing})
            return
        super()._dispatch(msg)


def canned_caller(case: dict[str, Any], log: Path) -> Caller:
    def call(name: str, args: Any) -> tuple[str, bool]:
        with log.open('a') as f:
            f.write(
                json.dumps({'tool': name, 'args': args if isinstance(args, dict) else {}}) + '\n'
            )
        if name not in tool_names():
            return f'{name} is not a tool.', True
        return case['answers'].get(name, 'Done.'), False

    return call


def serve(case_id: str, log: Path) -> None:
    case = next(c for c in load_cases() if c['id'] == case_id)

    def write(line: str) -> None:
        sys.stdout.write(line + '\n')
        sys.stdout.flush()

    server = EvalServer(canned_caller(case, log), write)
    for line in sys.stdin:
        server.handle_line(line)


def claude_command(case: dict[str, Any], mcp_config: Path, claude: str = 'claude') -> list[str]:
    skill = (SKILLS / 'orchestrate' / 'SKILL.md').read_text()
    allowed = [f'mcp__{SERVER_NAME}__{name}' for name in sorted(tool_names())]
    return [
        claude, '-p', case['request'],
        '--model', 'opus',
        '--output-format', 'json',
        '--max-turns', '8',
        '--strict-mcp-config', '--mcp-config', str(mcp_config),
        '--allowedTools', ' '.join(allowed),
        '--disallowedTools', 'Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit',
        '--append-system-prompt', PREAMBLE + skill,
    ]  # fmt: skip


def mcp_config(case_id: str, log: Path) -> dict[str, Any]:
    src = Path(__file__).resolve().parents[1] / 'src'
    return {
        'mcpServers': {
            SERVER_NAME: {
                'command': sys.executable,
                'args': [str(Path(__file__).resolve()), 'serve', case_id, str(log)],
                'env': {'PYTHONPATH': str(src)},
            }
        }
    }


if __name__ == '__main__':  # pragma: no cover - started by claude in the live eval
    serve(sys.argv[2], Path(sys.argv[3]))
