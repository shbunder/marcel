"""The Terrarium — a sealed world for scenario-testing Marcel.

A terrarium isolates every stateful surface a turn touches, runs the
**real** :func:`~marcel_core.harness.runner.stream_turn` against an odile
model double, and records what happened:

- **State**: the storage data root is redirected to a temp dir; the zoo dir
  is empty unless the scenario points one in. Two terrariums never share
  state.
- **Process globals**: the channel registry, extension registry, approval
  registry, and command-policy singleton are snapshotted on entry and
  restored on exit, so allow-always amendments or fake channels cannot leak
  between tests.
- **Observation**: recorder handlers ride the extension registry onto each
  turn's event bus, so a :class:`TurnResult` carries the bus events next to
  the harness stream events. (Recorders run *after* the core handlers; a
  denied ``tool_call`` short-circuits dispatch, so a denial is asserted via
  the harness ``ToolCallCompleted`` that carries the block reason.)
- **Approvals**: a fake channel plugin resolves ask-tier approval requests
  per the scenario's declared outcome — no human, no real waiting.
- **Network**: outbound httpx traffic goes to declared fakes
  (:meth:`Terrarium.fake_api`, odile's :class:`~odile.FakeWorld`) or raises.
  Model traffic is blocked suite-wide by the pytest plugin.

Use via the ``terrarium`` fixture (``marcel_testing.pytest_plugin``) or as
a context manager around a data-root path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from odile import FakeAPI, ScriptedModel
from odile.fake_api import FakeWorld
from odile.script import Step
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
class TurnResult:
    """Everything one scenario turn produced, for assertions.

    ``events`` are the harness stream events (``RunStarted`` …
    ``RunFinished``); ``bus_events`` are the lifecycle events recorded from
    the turn's event bus, in emission order.
    """

    reply: str
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
class Scenario:
    """One user turn against a model double inside a terrarium."""

    terrarium: Terrarium
    model: Model
    user_slug: str
    channel: str

    async def run(
        self,
        text: str,
        *,
        conversation_id: str = 'conv-1',
        check_script: bool = True,
    ) -> TurnResult:
        """Send ``text`` through the real turn loop and collect the result.

        With ``check_script`` (default), a :class:`~odile.ScriptedModel`
        must have played its whole script by the end of the turn — leftover
        steps are a failed expectation, not a pass.
        """
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
        if check_script and isinstance(self.model, ScriptedModel):
            self.model.assert_done()
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


class Terrarium:
    """The sealed world. See the module docstring for what it isolates.

    Usage as a fixture (preferred)::

        async def test_greeting(terrarium):
            scenario = terrarium.scenario(reply('Hello!'), user='alice')
            result = await scenario.run('hi marcel')
            assert result.reply == 'Hello!'
    """

    def __init__(self, data_root: Path, *, zoo_dir: Path | None = None) -> None:
        self.data_root = data_root
        self.zoo_dir = zoo_dir
        self._world = FakeWorld()
        self._bus_recording: list[Event] = []
        self._saved: dict[str, object] = {}
        self._entered = False

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> Terrarium:
        import marcel_core.harness.approval as approval_mod
        import marcel_core.harness.core_handlers as core_handlers_mod
        import marcel_core.plugin.channels as channels_mod
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

        # Persistence store — per-turn served/pending maps must start empty
        # in every sealed world (the singleton outlives worlds).
        from marcel_core.capabilities.persistence import persistence_store

        persistence_store().reset()

        # Memory stores cache by resolved data root; a new sealed root must
        # not reuse a store (and its journal) from a previous world.
        from marcel_core.capabilities.memory import reset_memory_stores

        reset_memory_stores()

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

        self._entered = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        import marcel_core.harness.approval as approval_mod
        import marcel_core.harness.core_handlers as core_handlers_mod
        import marcel_core.plugin.channels as channels_mod
        from marcel_core.config import settings
        from marcel_core.plugin.extension import extension_registry
        from marcel_core.storage import _root

        self._world.stop()

        channels_mod._registry.clear()
        channels_mod._registry.update(self._saved['channels'])  # type: ignore[arg-type]
        extension_registry().handlers[:] = self._saved['ext_handlers']  # type: ignore[assignment]
        core_handlers_mod._POLICY = self._saved['policy']
        approval_mod._REGISTRY = self._saved['approvals']
        settings.marcel_approval_timeout_seconds = cast(float, self._saved['approval_timeout'])

        from marcel_core.capabilities.persistence import persistence_store

        persistence_store().reset()

        from marcel_core.capabilities.memory import reset_memory_stores

        reset_memory_stores()

        settings.marcel_zoo_dir = cast('str | None', self._saved['marcel_zoo_dir'])
        settings.marcel_data_dir = cast('str | None', self._saved['marcel_data_dir'])
        _root._DATA_ROOT = self._saved['data_root']
        self._entered = False

    def _require_entered(self) -> None:
        if not self._entered:
            raise RuntimeError(
                'this Terrarium is not active — use it as a context manager '
                '(`with Terrarium(...) as t:`) or through the `terrarium` fixture.'
            )

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
        from marcel_core.storage.conversation import append_to_segment
        from marcel_core.storage.history import HistoryMessage

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

    def install_connector(self, park_dir: Path) -> None:
        """Install a connector habitat into the sealed world's zoo.

        The Terrarium seals with ``zoo_dir=None`` so scenarios see no habitats
        by accident; this is the explicit opt-in, mirroring :meth:`fake_api`'s
        declare-what-exists shape. The park is symlinked into a synthetic zoo
        under the sealed data root, so per-turn connector discovery
        (FEAT-260718-230bf8) finds exactly the parks a scenario declares —
        a deferred connector's tools then become callable after a scripted
        ``load_capability`` step, the production disclosure flow.
        """
        from marcel_core.config import settings

        self._require_entered()
        connectors_root = self.data_root / 'scenario-zoo' / 'connectors'
        connectors_root.mkdir(parents=True, exist_ok=True)
        link = connectors_root / park_dir.name
        if not link.exists():
            link.symlink_to(park_dir.resolve(), target_is_directory=True)
        # Entry saved marcel_zoo_dir; exit restores it — mid-run reassignment
        # is inside the sealed window.
        settings.marcel_zoo_dir = str(connectors_root.parent)

    def fake_api(self, base_url: str) -> FakeAPI:
        """Declare a fake external API at ``base_url``.

        Once any fake exists, unmatched outbound httpx traffic raises. See
        :mod:`odile.fake_api` for ``.returns(...)`` / ``.mount(app)``.
        """
        self._require_entered()
        return self._world.api(base_url)

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

    # -- running -------------------------------------------------------------

    def scenario(
        self,
        *steps: Step,
        model: Model | None = None,
        user: str = 'alice',
        channel: str = 'cli',
    ) -> Scenario:
        """Build a scenario from script ``steps`` (or an explicit ``model``).

        The user is seeded (default role) if it does not exist yet, so the
        one-liner ``terrarium.scenario(reply('hi'))`` just works.
        """
        self._require_entered()
        if model is None:
            model = ScriptedModel(*steps)
        elif steps:
            raise ValueError('pass either script steps or model=..., not both')
        if not (self.data_root / 'users' / user).is_dir():
            self.user(user)
        return Scenario(terrarium=self, model=model, user_slug=user, channel=channel)
