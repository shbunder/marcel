"""A fake `claude -p` for the brain: it replays a recorded brain turn instead of thinking (S-06.6).

The live eval (`test_skills_eval_live.py`) needs a real `claude` on the NUC. This fake takes the
very same command line (`skills_eval.claude_command`) and plays the turn recorded for the request in
`brain_replays.json`, as MCP `tools/call`s against a tools server, so the gate can check the whole
path without a model or the network:

- the skill in `--append-system-prompt` still says the rules the turn relies on (`skill_says`);
- every call is to a tool the server lists and the command allows, with arguments that fit the
  tool's `inputSchema` (as Claude Code checks them);
- the server's answers come back; an error answer stops the turn, as a failed run.

A request with no recording is refused ("is not faked"), as the runner's fake `claude` does.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

REPLAYS = Path(__file__).resolve().parent / 'brain_replays.json'

Send = Callable[[dict[str, Any]], dict[str, Any]]


class NotFaked(Exception):
    """The fake cannot play this command. The message says why."""


def load_replays() -> dict[str, Any]:
    return json.loads(REPLAYS.read_text())['replays']


def _option(argv: list[str], name: str) -> str:
    if name not in argv:
        raise NotFaked(f'claude was started without {name}.')
    return argv[argv.index(name) + 1]


class FakeBrain:
    def __init__(self, cases: list[dict[str, Any]], replays: dict[str, Any]) -> None:
        self.cases = cases
        self.replays = replays

    def run(self, argv: list[str], send: Send) -> dict[str, Any]:
        """Play the turn for `claude -p <request> …`; `send` is one JSON-RPC request to the MCP
        server, answered. Returns what `--output-format json` prints."""
        if argv[1] != '-p':
            raise NotFaked(f'only "claude -p" is faked, not {argv[1:2]}.')
        request = argv[2]
        case = next((c for c in self.cases if c['request'] == request), None)
        if case is None or case['id'] not in self.replays:
            raise NotFaked(f'no brain turn is recorded for "{request}"; it is not faked.')
        replay = self.replays[case['id']]
        prompt = ' '.join(_option(argv, '--append-system-prompt').split())
        for rule in replay['skill_says']:
            if ' '.join(rule.split()) not in prompt:
                raise NotFaked(f'{case["id"]}: the skill no longer says “{rule}”.')
        allowed = set(_option(argv, '--allowedTools').split())
        server = json.loads(Path(_option(argv, '--mcp-config')).read_text())['mcpServers']
        (name,) = server

        rid = 0

        def rpc(method: str, params: Any = None) -> Any:
            nonlocal rid
            rid += 1
            answer = send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
            if 'error' in answer:
                raise NotFaked(f'the MCP server answered {method} with {answer["error"]}.')
            return answer['result']

        rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}})
        listing = {tool['name']: tool for tool in rpc('tools/list')['tools']}
        last = ''
        for step in replay['calls']:
            tool, args = step['tool'], step['args']
            if f'mcp__{name}__{tool}' not in allowed or tool not in listing:
                raise NotFaked(f'{case["id"]}: {tool} is not a tool this brain may call.')
            try:
                jsonschema.validate(args, listing[tool]['inputSchema'])
            except jsonschema.ValidationError as exc:
                raise NotFaked(f'{case["id"]}: {tool} {args} does not fit: {exc.message}') from exc
            result = rpc('tools/call', {'name': tool, 'arguments': args})
            last = result['content'][0]['text']
            if result.get('isError'):
                return {'type': 'result', 'is_error': True, 'result': last}
        return {'type': 'result', 'is_error': False, 'result': last,
                'num_turns': len(replay['calls'])}  # fmt: skip
