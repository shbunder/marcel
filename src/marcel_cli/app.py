"""Marcel CLI — pure scrolling REPL, no full-screen TUI."""
from __future__ import annotations

import asyncio
import os

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.text import Text

from . import __version__ as _CLI_VERSION
from .chat import ChatClient, ConnectionState
from .config import Config
from .mascot import BLUSH_ROSE, _art

console = Console(highlight=False)

# Gray background applied by prompt_toolkit — no need to reprint user messages
_SESSION_STYLE = Style.from_dict({
    '':       'bg:#1e1e1e fg:#ffffff',
    'prompt': 'bg:#1e1e1e fg:#ffffff bold',
})
_PROMPT = FormattedText([('class:prompt', ' ❯  ')])

_COMMANDS: list[tuple[str, str]] = [
    ('/clear',      'Clear the screen'),
    ('/compact',    'Compact conversation context  [requires server]'),
    ('/cost',       'Show token usage and cost     [requires server]'),
    ('/help',       'Show available commands'),
    ('/memory',     'Show Marcel\'s memory          [requires server]'),
    ('/model',      'Show or set the current model'),
    ('/reconnect',  'Reconnect to the Marcel server'),
    ('/status',     'Show connection and server status'),
    ('/exit',       'Exit Marcel'),
    ('/quit',       'Exit Marcel'),
]

_SERVER_COMMANDS = {'/compact', '/cost', '/memory'}


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/'):
            return
        word = text.lstrip('/')
        for cmd, description in _COMMANDS:
            if cmd.lstrip('/').startswith(word):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=description,
                )


async def _fetch_server_version(config: Config) -> str:
    """Fetch the backend version from /health. Returns 'offline' on failure."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f'http://{config.host}:{config.port}/health')
            return r.json().get('version', '?')
    except Exception:
        return 'offline'


def _print_header(config: Config, server_version: str) -> None:
    art = _art().splitlines()
    pad = '   '
    srv_color = '#ff6b6b' if server_version == 'offline' else '#888888'
    info: list[tuple[str, str]] = [
        (f'CLI v{_CLI_VERSION}',          'bold white'),
        (f'Server v{server_version}',     srv_color),
        (config.model,                    '#888888'),
        (config.user,                     BLUSH_ROSE),
    ]
    info_offset = 1
    width = console.width or 60
    rule = Text('─' * width, style='#333333')
    console.print(rule)
    for i in range(5):
        art_line = art[i] if i < len(art) else ''
        info_idx = i - info_offset
        if 0 <= info_idx < len(info):
            label, style = info[info_idx]
            console.print(Text.assemble((art_line + pad, BLUSH_ROSE), (label, style)))
        else:
            console.print(Text(art_line, style=BLUSH_ROSE))
    console.print(rule)
    console.print()


def _handle_command(text: str, config: Config, client: ChatClient, server_version: str) -> bool:
    """Handle a local /command. Returns True if the REPL should exit."""
    cmd = text.split()[0].lower()

    if cmd in ('/exit', '/quit'):
        return True

    if cmd == '/clear':
        os.system('clear')
        _print_header(config, server_version)

    elif cmd == '/model':
        args = text.split()[1:]
        if args:
            config.model = args[0]
            client._model = args[0]
            console.print(Text(f'  model set to: {config.model}', style='#888888'))
        else:
            console.print(Text(f'  model: {config.model}', style='#888888'))
        console.print()

    elif cmd == '/status':
        state = client.state.name.lower()
        color = '#888888' if client.state == ConnectionState.CONNECTED else '#ff6b6b'
        srv_color = '#ff6b6b' if server_version == 'offline' else '#555555'
        console.print(Text(f'  server:  {config.host}:{config.port}', style='#555555'))
        console.print(Text(f'  status:  {state}', style=color))
        console.print(Text(f'  cli:     v{_CLI_VERSION}', style='#555555'))
        console.print(Text(f'  backend: v{server_version}', style=srv_color))
        console.print(Text(f'  model:   {config.model}', style='#555555'))
        console.print(Text(f'  user:    {config.user}', style='#555555'))
        console.print()

    elif cmd in _SERVER_COMMANDS:
        if client.state != ConnectionState.CONNECTED:
            console.print(Text(f'  {cmd} requires a running server. Try: make serve', style='#ff6b6b'))
            console.print()
            return False
        # caller handles the send
        return False

    elif cmd == '/reconnect':
        pass  # handled in caller

    elif cmd == '/help':
        console.print(Text('  Available commands:', style='bold white'))
        for c, desc in _COMMANDS:
            console.print(Text.assemble(('  ', ''), (f'{c:<14}', '#cc5e76'), ('  ' + desc, '#888888')))
        console.print()

    else:
        console.print(Text(f'  Unknown command: {cmd}. Type /help for a list.', style='#ff6b6b'))
        console.print()

    return False


async def run(config: Config) -> None:
    """Main REPL loop."""
    server_version = await _fetch_server_version(config)
    _print_header(config, server_version)

    client = ChatClient(ws_url=config.ws_url, user=config.user, token=config.token, model=config.model)

    try:
        await client.connect()
    except Exception as exc:
        console.print(Text(f'  Could not connect to Marcel server: {exc}', style='#ff6b6b'))
        console.print(Text('  Start the server with: make serve', style='#555555 italic'))
        console.print()

    session: PromptSession = PromptSession(
        completer=_SlashCompleter(),
        complete_while_typing=True,
        style=_SESSION_STYLE,
    )

    with patch_stdout(raw=True):
        while True:
            try:
                user_input = await session.prompt_async(_PROMPT)
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            text = user_input.strip()
            if not text:
                continue

            console.print()  # blank line after every input

            if text.startswith('/'):
                cmd = text.split()[0].lower()

                if cmd == '/reconnect':
                    console.print(Text('  Reconnecting…', style='#555555 italic'))
                    try:
                        await client.connect()
                        console.print(Text('  Connected.', style='#888888'))
                    except Exception as exc:
                        console.print(Text(f'  Reconnect failed: {exc}', style='#ff6b6b'))
                    console.print()
                    continue

                should_exit = _handle_command(text, config, client, server_version)
                if should_exit:
                    break

                # Server commands when connected fall through to send
                if cmd not in _SERVER_COMMANDS or client.state != ConnectionState.CONNECTED:
                    continue

            if client.state != ConnectionState.CONNECTED:
                console.print(Text('  Not connected. Try /reconnect or make serve', style='#555555 italic'))
                console.print()
                continue

            try:
                token_iter = await client.send(text)
                tokens: list[str] = []
                async for token in token_iter:
                    tokens.append(token)
                response = ''.join(tokens)
                console.print(Text.assemble(('● ', 'white'), (response, 'white')))
            except Exception as exc:
                console.print(Text(f'  Error: {exc}', style='#ff6b6b'))

            console.print()

    await client.disconnect()
