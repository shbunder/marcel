"""Marcel CLI — pure scrolling REPL, no full-screen TUI."""
from __future__ import annotations

import asyncio
import os
import random
import signal
import subprocess

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__ as _CLI_VERSION
from .chat import ChatClient, ConnectionState
from .config import Config, load_config, save_config
from .mascot import BLUSH_ROSE, _art

DEEP_TEAL = '#2ec4b6'
_SEP_COLOR = '#444444'

_WELCOME_MESSAGES = [
    'Welcome back, {user}!',
    'Good to see you, {user}.',
    'Ready when you are.',
    'At your service.',
    'What can I do for you today?',
    'Hello again, {user}.',
    "Let's get to work.",
    'How can I help?',
]

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
    ('/config',     'Show or set config  (/config host <value>)'),
    ('/cost',       'Show token usage and cost     [requires server]'),
    ('/help',       'Show available commands'),
    ('/memory',     'Show Marcel\'s memory          [requires server]'),
    ('/model',      'Show or set the current model'),
    ('/reconnect',  'Reconnect to the Marcel server'),
    ('/status',     'Show connection and server status'),
    ('/exit',       'Exit Marcel'),
    ('/quit',       'Exit Marcel'),
]

_CONFIG_FIELDS = {'host', 'port', 'user', 'model', 'token'}

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


def _print_header(config: Config, server_version: str, connected: bool = False) -> None:
    art = _art().splitlines()[:6]  # head only — keeps height tight
    welcome = random.choice(_WELCOME_MESSAGES).format(user=config.user)
    D = '│ '  # column divider baked into content

    # col1: welcome + mascot head (7 rows total)
    col1 = Text(no_wrap=True)
    col1.append(welcome + '\n', style='bold white')
    for line in art:
        col1.append(line + '\n', style='#cc5e76')

    # col2: runtime info (5 rows)
    col2 = Text(no_wrap=True)
    col2.append(D, style=_SEP_COLOR); col2.append('Runtime\n', style='bold #888888')
    col2.append(D, style=_SEP_COLOR); col2.append('─' * 20 + '\n', style='#333333')
    col2.append(D, style=_SEP_COLOR); col2.append('cli    ', style='#555555'); col2.append(f'v{_CLI_VERSION}\n', style='#888888')
    col2.append(D, style=_SEP_COLOR); col2.append('user   ', style='#555555'); col2.append(f'{config.user}\n', style=DEEP_TEAL)
    col2.append(D, style=_SEP_COLOR); col2.append('model  ', style='#555555'); col2.append(f'{config.model}\n', style='#888888')

    # col3: server info (7 rows)
    srv_color = '#ff6b6b' if server_version == 'offline' else '#888888'
    conn_label = '● connected' if connected else '● offline'
    conn_color = '#4caf50' if connected else '#ff6b6b'
    col3 = Text(no_wrap=True)
    col3.append(D, style=_SEP_COLOR); col3.append('Server\n', style='bold #888888')
    col3.append(D, style=_SEP_COLOR); col3.append('─' * 20 + '\n', style='#333333')
    col3.append(D, style=_SEP_COLOR); col3.append('version  ', style='#555555'); col3.append(f'{server_version}\n', style=srv_color)
    col3.append(D, style=_SEP_COLOR); col3.append('host     ', style='#555555'); col3.append(f'{config.host}\n', style='#888888')
    col3.append(D, style=_SEP_COLOR); col3.append('port     ', style='#555555'); col3.append(f'{config.port}\n', style='#888888')
    col3.append(D, style=_SEP_COLOR); col3.append('─' * 20 + '\n', style='#333333')
    col3.append(D, style=_SEP_COLOR); col3.append(conn_label + '\n', style=conn_color)

    width = console.width or 80
    table = Table(show_header=False, show_edge=False, box=None, expand=True, padding=(0, 1))
    if width >= 88:
        table.add_column(min_width=13, no_wrap=True)
        table.add_column(min_width=24, no_wrap=True)
        table.add_column(min_width=24, no_wrap=True)
        table.add_row(col1, col2, col3)
    elif width >= 60:
        table.add_column(min_width=13, no_wrap=True)
        table.add_column(min_width=24, no_wrap=True)
        table.add_row(col1, col2)
    else:
        table.add_column(no_wrap=True)
        table.add_row(col1)

    console.print(Panel(
        table,
        title=f'[bold #cc5e76] Marcel CLI v{_CLI_VERSION} [/]',
        border_style='#cc5e76',
        padding=(0, 1),
    ))
    console.print()


def _handle_command(text: str, config: Config, client: ChatClient, server_version: str) -> bool:
    """Handle a local /command. Returns True if the REPL should exit."""
    cmd = text.split()[0].lower()

    if cmd in ('/exit', '/quit'):
        return True

    if cmd == '/clear':
        os.system('clear')
        _print_header(config, server_version, connected=client.state == ConnectionState.CONNECTED)

    elif cmd == '/model':
        args = text.split()[1:]
        if args:
            config.model = args[0]
            client._model = args[0]
            console.print(Text(f'  model set to: {config.model}', style='#888888'))
        else:
            console.print(Text(f'  model: {config.model}', style='#888888'))
        console.print()

    elif cmd == '/config':
        args = text.split()[1:]
        if not args:
            return False  # handled in async loop (opens nano)
        elif len(args) < 2:
            console.print(Text(f'  Usage: /config <field> <value>', style='#ff6b6b'))
            console.print(Text(f'  Fields: {", ".join(sorted(_CONFIG_FIELDS))}', style='#888888'))
        elif args[0] not in _CONFIG_FIELDS:
            console.print(Text(f'  Unknown field: {args[0]}. Options: {", ".join(sorted(_CONFIG_FIELDS))}', style='#ff6b6b'))
        else:
            field, value = args[0], args[1]
            if field == 'port':
                try:
                    value = int(value)  # type: ignore[assignment]
                except ValueError:
                    console.print(Text('  port must be a number', style='#ff6b6b'))
                    console.print()
                    return False
            setattr(config, field, value)
            if field == 'model':
                client._model = str(value)
            save_config(config)
            console.print(Text(f'  {field} → {value}', style='#888888'))
            if field in ('host', 'port'):
                console.print(Text('  Run /reconnect to connect to the new server.', style='#555555 italic'))
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


def _layout_tier(width: int) -> int:
    """Return the current layout tier based on terminal width (3 / 2 / 1 columns)."""
    if width >= 88:
        return 3
    if width >= 60:
        return 2
    return 1


async def run(config: Config) -> None:
    """Main REPL loop."""
    server_version = await _fetch_server_version(config)

    client = ChatClient(ws_url=config.ws_url, user=config.user, token=config.token, model=config.model)

    try:
        await client.connect()
    except Exception as exc:
        console.print(Text(f'  Could not connect to Marcel server: {exc}', style='#ff6b6b'))
        console.print(Text('  Start the server with: make serve', style='#555555 italic'))
        console.print()

    _print_header(config, server_version, connected=client.state == ConnectionState.CONNECTED)

    _resize_task: asyncio.Task | None = None

    async def _redraw_after_delay() -> None:
        await asyncio.sleep(0.12)  # debounce: wait for resize to settle
        os.system('clear')
        _print_header(config, server_version, connected=client.state == ConnectionState.CONNECTED)

    def _on_resize() -> None:
        nonlocal _resize_task
        if _resize_task and not _resize_task.done():
            _resize_task.cancel()
        _resize_task = asyncio.get_event_loop().create_task(_redraw_after_delay())

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGWINCH, _on_resize)

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
                        server_version = await _fetch_server_version(config)
                        os.system('clear')
                        _print_header(config, server_version, connected=True)
                    except Exception as exc:
                        console.print(Text(f'  Reconnect failed: {exc}', style='#ff6b6b'))
                        console.print()
                    continue

                if cmd == '/config' and len(text.split()) == 1:
                    loop = asyncio.get_event_loop()
                    from .config import _CONFIG_PATH
                    await loop.run_in_executor(None, lambda: subprocess.run(['nano', str(_CONFIG_PATH)]))
                    fresh = load_config()
                    config.host, config.port = fresh.host, fresh.port
                    config.user, config.token, config.model = fresh.user, fresh.token, fresh.model
                    client._model = config.model
                    client._user = config.user
                    client._token = config.token
                    server_version = await _fetch_server_version(config)
                    os.system('clear')
                    _print_header(config, server_version, connected=client.state == ConnectionState.CONNECTED)
                    console.print(Text('  Config reloaded.', style='#888888'))
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

    loop.remove_signal_handler(signal.SIGWINCH)
    await client.disconnect()
