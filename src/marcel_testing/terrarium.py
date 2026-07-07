"""The Terrarium — Marcel's binding of odile's sealed world.

:class:`odile.Terrarium`/:class:`odile.Scenario`/:class:`odile.TurnResult`
carry the generic sealed-world discipline (lifecycle, entered guard, fake
APIs, script rules); this module implements their hooks for the Marcel
kernel, so a scenario runs the **real**
:func:`~marcel_core.harness.runner.stream_turn` and records what happened:

- **State** (``_seal``/``_restore``): the storage data root is redirected to
  a temp dir; the zoo dir is empty unless the scenario points one in. Two
  terrariums never share state.
- **Process globals** (``_seal``/``_restore``): the channel registry,
  extension registry, approval registry, and command-policy singleton are
  snapshotted on entry and restored on exit, so allow-always amendments or
  fake channels cannot leak between tests.
- **Observation**: recorder handlers ride the extension registry onto each
  turn's event bus, so a :class:`TurnResult` carries the bus events next to
  the harness stream events. (Recorders run *after* the core handlers; a
  denied ``tool_call`` short-circuits dispatch, so a denial is asserted via
  the harness ``ToolCallCompleted`` that carries the block reason.)
- **Approvals**: a fake channel plugin resolves ask-tier approval requests
  per the scenario's declared outcome — no human, no real waiting.
- **Network**: outbound httpx traffic goes to declared fakes
  (:meth:`odile.Terrarium.fake_api`) or raises. Model traffic is blocked
  suite-wide by the pytest plugin.

Use via the ``terrarium`` fixture (``marcel_testing.pytest_plugin``) or as
a context manager around a data-root path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from odile.terrarium import Scenario as OdileScenario, Terrarium as OdileTerrarium, TurnResult as OdileTurnResult
from pydantic_ai.models import Model

if TYPE_CHECKING:
    from marcel_core.harness.runner import MarcelEvent, RunFinished, ToolCallCompleted
    from marcel_sdk.events import Event, ToolCallEvent, ToolResultEvent

# Approval outcomes a scenario can declare, mirroring
# ``marcel_core.harness.approval.ApprovalOutcome`` values, plus ``'expire'``
# (deliver the prompt, never answer → the registry times out to a queued
# deny). Strings so scenario code doesn't need kernel imports.
APPROVAL_POLICIES = ('allow_once', 'allow_always', 'deny', 'expire')

# When a scenario declares approvals should expire, waiting the configured
# real timeout would violate the no-real-waiting rule — the terrarium drops
# it to something the event loop clears almost immediately.
_EXPIRE_TIMEOUT_SECONDS = 0.05


@dataclass
class TurnResult(OdileTurnResult):
    """Everything one scenario turn produced, for assertions.

    ``events`` are the harness stream events (``RunStarted`` …
    ``RunFinished``); ``bus_events`` are the lifecycle events recorded from
    the turn's event bus, in emission order.
    """

    events: list[MarcelEvent]
    bus_events: list[Event]

    def bus(self, name: str) -> list[Event]:
        """Recorded bus events with ``NAME == name`` (e.g. ``'tool_call'``)."""
        return [e for e in self.bus_events if e.NAME == name]

    @property
    def tool_calls(self) -> list[ToolCallEvent]:
        """Recorded (non-denied) ``tool_call`` events, final args included."""
        return self.bus('tool_call')  # type: ignore[return-value]

    @property
    def tool_results(self) -> list[ToolResultEvent]:
        """Recorded ``tool_result`` events."""
        return self.bus('tool_result')  # type: ignore[return-value]

    @property
    def completions(self) -> list[ToolCallCompleted]:
        """Harness tool completions — includes denied calls, whose ``result``
        carries the block reason."""
        return [e for e in self.events if e.type == 'tool_call_completed']  # type: ignore[misc]

    @property
    def finished(self) -> RunFinished:
        """The turn's ``RunFinished`` event."""
        return next(e for e in self.events if e.type == 'run_finished')  # type: ignore[return-value]

    @property
    def is_error(self) -> bool:
        """Whether the turn finished in an error state."""
        return bool(self.finished.is_error)


@dataclass
class Scenario(OdileScenario[TurnResult]):
    """One user turn against a model double inside a terrarium.

    ``run()`` (inherited) accepts ``conversation_id='conv-1'`` and the
    generic ``check_script=True``; the drive hook streams the turn through
    the real harness.
    """

    terrarium: Terrarium
    user_slug: str
    channel: str

    async def _drive(self, text: str, *, conversation_id: str = 'conv-1', **turn_kwargs: Any) -> TurnResult:
        """Send ``text`` through the real turn loop and collect the result."""
        if turn_kwargs:
            raise TypeError(
                f'unknown run option(s): {", ".join(sorted(turn_kwargs))} — supported: conversation_id, check_script'
            )
        from marcel_core.harness.runner import TextDelta, stream_turn

        self.terrarium._bus_recording.clear()
        events = [
            event
            async for event in stream_turn(
                self.user_slug,
                self.channel,
                text,
                conversation_id,
                model=self.model,
            )
        ]
        reply = ''.join(e.text for e in events if isinstance(e, TextDelta))
        return TurnResult(reply=reply, events=events, bus_events=list(self.terrarium._bus_recording))


@dataclass
class _ApprovalChannel:
    """Fake channel plugin that auto-resolves approval prompts.

    Registered under the scenario's channel name so the command policy's
    ask path (``core_handlers._request_approval``) finds a
    ``send_approval_request`` — then answers it per ``policy`` without a
    human. ``'expire'`` delivers but never answers, so the registry times
    out to a queued deny.
    """

    name: str
    policy: str
    requests: list[dict] = field(default_factory=list)
    # Channel plugins are duck-typed; the prompt builder reads
    # ``plugin.capabilities.rich_ui``, so a minimal capabilities shape must exist.
    capabilities: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(
            rich_ui=False, markdown=False, streaming=False, progress_updates=False, attachments=False
        )
    )

    async def send_approval_request(self, payload: dict) -> bool:
        from marcel_core.harness.approval import ApprovalOutcome, approval_registry

        self.requests.append(payload)
        if self.policy == 'expire':
            return True

        outcome = ApprovalOutcome(self.policy)
        registry = approval_registry()

        async def _resolve_once_waiting() -> None:
            # ``wait()`` registers the pending future only after this send
            # returns — yield until the resolution lands (bounded so a bug
            # cannot spin forever).
            for _ in range(100):
                if registry.resolve(payload['id'], outcome):
                    return
                await asyncio.sleep(0)

        asyncio.get_running_loop().create_task(_resolve_once_waiting())
        return True


class Terrarium(OdileTerrarium[Scenario]):
    """The sealed Marcel world. See the module docstring for what it seals.

    Usage as a fixture (preferred)::

        async def test_greeting(terrarium):
            scenario = terrarium.scenario(reply('Hello!'), user='alice')
            result = await scenario.run('hi marcel')
            assert result.reply == 'Hello!'
    """

    def __init__(self, data_root: Path, *, zoo_dir: Path | None = None) -> None:
        super().__init__()
        self.data_root = data_root
        self.zoo_dir = zoo_dir
        self._bus_recording: list[Event] = []
        self._saved: dict[str, object] = {}

    # -- lifecycle hooks (odile calls these on enter/exit) --------------------

    def _seal(self) -> None:
        import marcel_core.harness.approval as approval_mod
        import marcel_core.harness.core_handlers as core_handlers_mod
        import marcel_core.plugin.channels as channels_mod
        import marcel_core.skills.registry as skills_registry_mod
        import marcel_core.toolkit as toolkit_mod
        from marcel_core.config import settings
        from marcel_core.plugin.extension import extension_registry
        from marcel_core.storage import _root
        from marcel_sdk import events as events_mod

        self.data_root.mkdir(parents=True, exist_ok=True)

        # State roots — both seams, so storage helpers and the few direct
        # ``settings.data_dir`` readers see the same sealed directory. The
        # ``data_dir`` / ``zoo_dir`` properties derive from these fields.
        self._saved['data_root'] = _root._DATA_ROOT
        _root._DATA_ROOT = self.data_root
        self._saved['marcel_data_dir'] = settings.marcel_data_dir
        settings.marcel_data_dir = str(self.data_root)
        self._saved['marcel_zoo_dir'] = settings.marcel_zoo_dir
        settings.marcel_zoo_dir = str(self.zoo_dir) if self.zoo_dir is not None else None

        # Process-global registries — snapshot, then restore on exit.
        self._saved['channels'] = dict(channels_mod._registry)
        self._saved['ext_handlers'] = list(extension_registry().handlers)
        self._saved['policy'] = core_handlers_mod._POLICY
        core_handlers_mod._POLICY = None  # fresh policy; allow-always can't leak
        self._saved['approvals'] = approval_mod._REGISTRY
        approval_mod._REGISTRY = approval_mod.ApprovalRegistry()
        self._saved['approval_timeout'] = settings.marcel_approval_timeout_seconds
        # Toolkit handlers a scenario registers via @marcel_tool must not
        # outlive the world; the skills-registry cache derives from them and
        # is dropped on both entry and exit so each world re-resolves.
        self._saved['toolkit_registry'] = dict(toolkit_mod._registry)
        self._saved['toolkit_metadata'] = dict(toolkit_mod._metadata)
        skills_registry_mod._cache = None
        skills_registry_mod._cache_mtime = None

        # Bus recorder — rides the extension registry onto every turn's bus.
        def _record(event: events_mod.Event, ctx: object) -> None:
            self._bus_recording.append(event)

        for event_cls in vars(events_mod).values():
            if (
                isinstance(event_cls, type)
                and issubclass(event_cls, events_mod.Event)
                and event_cls is not events_mod.Event
            ):
                extension_registry().handlers.append((event_cls.NAME, _record))

    def _restore(self) -> None:
        import marcel_core.harness.approval as approval_mod
        import marcel_core.harness.core_handlers as core_handlers_mod
        import marcel_core.plugin.channels as channels_mod
        import marcel_core.skills.registry as skills_registry_mod
        import marcel_core.toolkit as toolkit_mod
        from marcel_core.config import settings
        from marcel_core.plugin.extension import extension_registry
        from marcel_core.storage import _root

        toolkit_mod._registry.clear()
        toolkit_mod._registry.update(self._saved['toolkit_registry'])  # type: ignore[arg-type]
        toolkit_mod._metadata.clear()
        toolkit_mod._metadata.update(self._saved['toolkit_metadata'])  # type: ignore[arg-type]
        skills_registry_mod._cache = None
        skills_registry_mod._cache_mtime = None

        channels_mod._registry.clear()
        channels_mod._registry.update(self._saved['channels'])  # type: ignore[arg-type]
        extension_registry().handlers[:] = self._saved['ext_handlers']  # type: ignore[assignment]
        core_handlers_mod._POLICY = self._saved['policy']
        approval_mod._REGISTRY = self._saved['approvals']
        settings.marcel_approval_timeout_seconds = cast(float, self._saved['approval_timeout'])

        settings.marcel_zoo_dir = cast('str | None', self._saved['marcel_zoo_dir'])
        settings.marcel_data_dir = cast('str | None', self._saved['marcel_data_dir'])
        _root._DATA_ROOT = self._saved['data_root']

    # -- world building ------------------------------------------------------

    def user(
        self,
        slug: str = 'alice',
        *,
        role: str = 'user',
        profile: str = '',
        memories: dict[str, str] | None = None,
    ) -> str:
        """Seed a user: role (profile.md frontmatter), profile body, memories.

        ``memories`` maps ``name`` → markdown body, written to the user's
        memory dir so the turn's memory index sees them.
        """
        self._require_entered()
        from marcel_core.storage.users import save_user_profile, set_user_role

        set_user_role(slug, role)
        if profile:
            save_user_profile(slug, profile)
        for name, body in (memories or {}).items():
            memory_dir = self.data_root / 'users' / slug / 'memory'
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / f'{name}.md').write_text(body, encoding='utf-8')
        return slug

    def seed_history(self, user_slug: str, channel: str, turns: list[tuple[str, str]]) -> None:
        """Pre-load conversation history as ``(role, text)`` tuples."""
        self._require_entered()
        from marcel_core.memory.conversation import append_to_segment
        from marcel_core.memory.history import HistoryMessage

        for msg_role, text in turns:
            append_to_segment(
                user_slug,
                channel,
                HistoryMessage(
                    role=msg_role,  # type: ignore[arg-type]
                    text=text,
                    timestamp=datetime.now(tz=timezone.utc),
                    conversation_id='seeded',
                ),
            )

    def resolve_approvals(self, policy: str, *, channel: str = 'cli') -> list[dict]:
        """Declare how ask-tier approval prompts on ``channel`` resolve.

        ``policy`` is one of ``'allow_once'``, ``'allow_always'``,
        ``'deny'``, ``'expire'``. Returns the live list that collects each
        approval request payload as it is delivered, for assertions.
        """
        self._require_entered()
        from marcel_core.config import settings
        from marcel_core.plugin.channels import register_channel

        if policy not in APPROVAL_POLICIES:
            raise ValueError(f'unknown approval policy {policy!r} — expected one of {APPROVAL_POLICIES}')
        if policy == 'expire':
            settings.marcel_approval_timeout_seconds = _EXPIRE_TIMEOUT_SECONDS
        plugin = _ApprovalChannel(name=channel, policy=policy)
        register_channel(plugin)  # type: ignore[arg-type]
        return plugin.requests

    # -- running (scenario() itself is inherited from odile) ------------------

    def _build_scenario(self, model: Model, **context: Any) -> Scenario:
        """Build Marcel's scenario: ``user`` (default ``'alice'``) and
        ``channel`` (default ``'cli'``) are the supported context keys.

        The user is seeded (default role) if it does not exist yet, so the
        one-liner ``terrarium.scenario(reply('hi'))`` just works.
        """
        user = cast(str, context.pop('user', 'alice'))
        channel = cast(str, context.pop('channel', 'cli'))
        if context:
            raise TypeError(
                f'unknown scenario option(s): {", ".join(sorted(context))} — supported: model, user, channel'
            )
        if not (self.data_root / 'users' / user).is_dir():
            self.user(user)
        return Scenario(model=model, terrarium=self, user_slug=user, channel=channel)
