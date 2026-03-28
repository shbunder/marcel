"""Textual application for the Marcel CLI."""
from __future__ import annotations

import asyncio

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Input, RichLog, Static

from .chat import ChatClient, ConnectionState
from .config import Config
from .mascot import mascot_text  # noqa: F401 (used in welcome message)


_STATUS_CONNECTED = '[bold green]connected ●[/]'
_STATUS_CONNECTING = '[yellow]connecting…[/]'
_STATUS_DISCONNECTED = '[red]disconnected ○[/]'


class MarcelApp(App):
    """Marcel chat TUI.

    Args:
        config: Resolved :class:`~marcel_cli.config.Config`.
    """

    CSS = '''
    Screen {
        background: $background;
    }
    #conversation {
        border: solid $primary;
        padding: 0 1;
        height: 1fr;
    }
    #status-bar {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
        content-align: right middle;
    }
    #input-row {
        height: 3;
        border: solid $primary;
    }
    #input {
        width: 1fr;
    }
    '''

    BINDINGS = [
        ('ctrl+q', 'quit', 'Quit'),
        ('ctrl+c', 'quit', 'Quit'),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._client = ChatClient(
            ws_url=config.ws_url,
            user=config.user,
            token=config.token,
        )
        self._streaming_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(_STATUS_CONNECTING, id='status-bar')
        yield RichLog(id='conversation', markup=True, wrap=True, highlight=False)
        with Horizontal(id='input-row'):
            yield Input(placeholder='Message Marcel…', id='input')

    async def on_mount(self) -> None:
        self.title = 'Marcel'
        self.sub_title = ''
        asyncio.create_task(self._connect())

    async def _connect(self) -> None:
        try:
            await self._client.connect()
            self._set_status(_STATUS_CONNECTED)
            log = self.query_one('#conversation', RichLog)
            from .mascot import mascot_text
            log.write(mascot_text())
            log.write('')
        except Exception as exc:
            self._set_status(_STATUS_DISCONNECTED)
            self._append_system(f'Connection failed: {exc}')

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one('#status-bar', Static).update(markup)
        except NoMatches:
            pass

    def _append_user(self, text: str) -> None:
        log = self.query_one('#conversation', RichLog)
        log.write(Text.assemble(('You: ', 'bold'), text))
        log.write('')

    def _append_assistant_response(self, response: str) -> None:
        log = self.query_one('#conversation', RichLog)
        label = Text.assemble(('Marcel: ', 'bold #cc5e76'))
        log.write(label)
        log.write(response)
        log.write('')

    def _append_system(self, text: str) -> None:
        log = self.query_one('#conversation', RichLog)
        log.write(Text(text, style='dim italic'))
        log.write('')

    @on(Input.Submitted)
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        input_widget = self.query_one('#input', Input)
        input_widget.value = ''
        input_widget.disabled = True

        self._append_user(text)

        if self._client.state != ConnectionState.CONNECTED:
            self._append_system('Not connected to Marcel. Reconnecting…')
            asyncio.create_task(self._connect())
            input_widget.disabled = False
            return

        self._streaming_task = asyncio.create_task(self._stream_response(text))

    async def _stream_response(self, text: str) -> None:
        input_widget = self.query_one('#input', Input)
        try:
            token_iter = await self._client.send(text)
            tokens: list[str] = []
            async for token in token_iter:
                tokens.append(token)
            self._append_assistant_response(''.join(tokens))
        except Exception as exc:
            self._append_system(f'Error: {exc}')
        finally:
            input_widget.disabled = False
            input_widget.focus()

    async def on_unmount(self) -> None:
        if self._streaming_task is not None:
            self._streaming_task.cancel()
        await self._client.disconnect()
