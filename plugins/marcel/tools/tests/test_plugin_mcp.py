"""The plugin's extra MCP servers (`plugins/marcel/.mcp.json`, S-15.1): Harry, and no secret in git.

Claude Code loads `.mcp.json` at the plugin root next to the servers inline in `plugin.json`
(`marcel`, `tools`); a server with the same name would replace the inline one.
"""

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).parents[2]
MCP_JSON = PLUGIN / '.mcp.json'
DOC = PLUGIN.parents[1] / 'docs' / 'connectors.md'
TOKEN_VAR = 'MARCEL_HARRY_TOKEN'
# The runner drops every variable with these prefixes before it starts claude
# (runner/src/marcel_runner/claude_env.py), so a token named like that would never arrive.
SCRUBBED_PREFIXES = ('CLAUDE', 'ANTHROPIC_')
VAR_REF = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}')


def servers() -> dict[str, dict[str, object]]:
    return json.loads(MCP_JSON.read_text())['mcpServers']


def test_harry_is_registered_over_http_on_the_nuc() -> None:
    assert servers()['harry'] == {
        'type': 'http',
        'url': 'http://localhost:7430/mcp',
        'headers': {'Authorization': f'Bearer ${{{TOKEN_VAR}}}'},
    }


def test_no_server_replaces_one_from_plugin_json() -> None:
    inline = json.loads((PLUGIN / '.claude-plugin' / 'plugin.json').read_text())['mcpServers']
    assert not set(servers()) & set(inline), (
        'a server in .mcp.json would replace the one in plugin.json'
    )


def test_every_header_takes_its_secret_from_the_environment() -> None:
    for name, server in servers().items():
        headers = server.get('headers', {})
        assert isinstance(headers, dict)
        for key, value in headers.items():
            rest = VAR_REF.sub('', value)
            assert rest in ('', 'Bearer ', 'Bearer'), (
                f'{name}: header {key} holds a literal value, not ${{VAR}}'
            )
            assert VAR_REF.search(value), f'{name}: header {key} names no environment variable'


def test_no_literal_secret_anywhere_in_the_file() -> None:
    text = VAR_REF.sub('', MCP_JSON.read_text())
    assert not re.search(r'[A-Za-z0-9_\-+/=.]{24,}', text), (
        'something long and token-shaped is in .mcp.json'
    )


def test_the_token_variable_survives_the_runner_scrub() -> None:
    names = {m.group(1) for m in VAR_REF.finditer(MCP_JSON.read_text())}
    assert TOKEN_VAR in names
    for name in names:
        assert not name.startswith(SCRUBBED_PREFIXES), (
            f'the runner removes {name} before claude starts'
        )


def test_the_connectors_doc_names_the_variable_and_the_server() -> None:
    text = DOC.read_text()
    assert TOKEN_VAR in text
    assert 'mcp__plugin_marcel_harry__' in text
    assert 'http://localhost:7430/mcp' in text
