"""Agent runner — streams events from pydantic-ai for one conversation turn.

Creates a stateless agent per turn, building context from segment-based
conversation history and dynamically selected memories.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from marcel_core.capabilities.persistence import conversation_key, persistence_store
from marcel_core.capabilities.persistence.extract import extract_tool_history
from marcel_core.harness.agent import create_marcel_agent
from marcel_core.harness.context import MarcelDeps
from marcel_core.harness.core_handlers import register_core_handlers
from marcel_core.harness.model_chain import (
    Tier,
    TierEntry,
    build_chain,
    build_explain_system_prompt,
    build_explain_user_prompt,
    is_fallback_eligible,
    model_label,
    next_tier,
)
from marcel_core.harness.tier_classifier import (
    classify_initial_tier,
    load_routing_config,
    maybe_bump_tier,
)
from marcel_core.harness.turn_router import (
    AdminTierConfig,
    TierSource,
    TurnPlan,
    resolve_turn,
)
from marcel_core.memory.conversation import append_to_segment
from marcel_core.memory.history import HistoryMessage
from marcel_core.storage.settings import (
    load_channel_model,
    load_channel_tier,
    save_channel_tier,
)
from marcel_core.storage.users import get_user_role
from marcel_sdk.events import (
    AgentEndEvent,
    BeforeAgentStartEvent,
    BeforeProviderRequestEvent,
    EventBus,
    EventContext,
    InputEvent,
    SessionStartEvent,
)

log = logging.getLogger(__name__)


async def build_context(
    user_slug: str,
    channel: str,
) -> list[ModelMessage]:
    """Build the context window for a conversation turn.

    Thin wrapper over the persistence store's loader (FEAT-260718-ed6d63):
    idle summarization, rolling summary prefix, active-segment conversion,
    and served-length bookkeeping for snapshot deltas all live there.
    """
    return await persistence_store().load_context(user_slug, channel)


@dataclass
class MarcelEvent:
    """Base class for events streamed during a turn."""

    type: str


@dataclass
class RunStarted(MarcelEvent):
    """Turn execution started."""

    type: Literal['run_started'] = 'run_started'  # type: ignore[assignment]
    conversation_id: str = ''


@dataclass
class TextDelta(MarcelEvent):
    """Incremental text from assistant."""

    type: Literal['text_delta'] = 'text_delta'  # type: ignore[assignment]
    text: str = ''


@dataclass
class ToolCallStarted(MarcelEvent):
    """Tool invocation started."""

    type: Literal['tool_call_started'] = 'tool_call_started'  # type: ignore[assignment]
    tool_call_id: str = ''
    tool_name: str = ''


@dataclass
class ToolCallCompleted(MarcelEvent):
    """Tool invocation completed."""

    type: Literal['tool_call_completed'] = 'tool_call_completed'  # type: ignore[assignment]
    tool_call_id: str = ''
    tool_name: str = ''
    result: str = ''
    is_error: bool = False


@dataclass
class A2UIComponent(MarcelEvent):
    """A2UI component payload emitted by the agent.

    Carries a declarative component description for the frontend to render.
    Part of the A2UI protocol — see ISSUE-063 for details.
    """

    type: Literal['a2ui_component'] = 'a2ui_component'  # type: ignore[assignment]
    component: str = ''
    props: dict[str, object] | None = None
    artifact_id: str | None = None


@dataclass
class RunFinished(MarcelEvent):
    """Turn execution finished."""

    type: Literal['run_finished'] = 'run_finished'  # type: ignore[assignment]
    total_cost_usd: float | None = None
    is_error: bool = False


def _loaded_skill_names(messages: Sequence[ModelMessage], extra: set[str] | None = None) -> set[str]:
    """Skill names loaded this conversation — the tier-influence input.

    Scans history for ``load_capability`` calls (a skill's capability id is
    its name); their pairs survive compaction (the framework preserves
    load-capability state). ``extra`` folds in a same-turn ``/<skill>``
    override, which force-loads a skill before the model runs.
    """
    from pydantic_ai.messages import LoadCapabilityCallPart

    names: set[str] = set(extra) if extra else set()
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            # ``capability_id`` is the typed part's property (parsed from the
            # load-capability args); a skill's capability id is its name.
            if isinstance(part, LoadCapabilityCallPart) and isinstance(part.capability_id, str) and part.capability_id:
                names.add(part.capability_id)
    return names


_TIER_RANK = {'fast': 1, 'standard': 2, 'power': 3}
_TIER_FROM_STR = {'fast': Tier.FAST, 'standard': Tier.STANDARD, 'power': Tier.POWER}


def _active_skill_tier(user_slug: str, active_names: set[str], role: str = 'user') -> tuple[Tier, str] | None:
    """Highest ``marcel-tier`` among skills active this conversation.

    POWER beats STANDARD beats FAST — a demanding skill wins. Returns
    ``(tier, skill_name)`` or ``None`` when no active skill declares a tier.
    """
    if not active_names:
        return None
    from marcel_core.skills.loader import load_skills

    best: tuple[Tier, str] | None = None
    best_rank = 0
    for doc in load_skills(user_slug, role):
        if doc.name not in active_names or not doc.preferred_tier:
            continue
        rank = _TIER_RANK.get(doc.preferred_tier, 0)
        if rank > best_rank:
            best_rank = rank
            best = (_TIER_FROM_STR[doc.preferred_tier], doc.name)
    return best


def _resolve_session_tier(
    user_slug: str,
    channel: str,
    user_text: str,
) -> tuple[Tier, str]:
    """Resolve the per-channel session tier, running classifier + bump as needed.

    Stateful: on a fresh session the classifier output is persisted to
    ``channel_tiers``; on frustration the bumped tier overwrites it. Returns
    ``(tier, reason)`` where ``reason`` is a log-friendly description of how
    the tier was picked.
    """
    cfg = load_routing_config()
    stored = load_channel_tier(user_slug, channel)
    if stored is None:
        session_tier, classify_reason = classify_initial_tier(user_text, cfg)
        save_channel_tier(user_slug, channel, session_tier.value)
        reason = f'classified:{classify_reason}'
    else:
        try:
            session_tier = Tier(stored)
        except ValueError:
            log.warning(
                'tier_resolver: invalid stored tier %r for (%s,%s) — reclassifying',
                stored,
                user_slug,
                channel,
            )
            session_tier, classify_reason = classify_initial_tier(user_text, cfg)
            save_channel_tier(user_slug, channel, session_tier.value)
            reason = f'classified:{classify_reason}'
        else:
            reason = f'session:{session_tier.value}'

    bumped, bump_reason = maybe_bump_tier(session_tier, user_text, cfg)
    if bumped != session_tier:
        save_channel_tier(user_slug, channel, bumped.value)
        return bumped, f'frustration_bump:{bump_reason}'

    return session_tier, reason


def _resolve_turn_tier(
    user_slug: str,
    channel: str,
    user_text: str,
    active_skill_names: set[str],
    role: str = 'user',
) -> tuple[Tier, str]:
    """Decide which tier this interactive turn runs on.

    Thin wrapper: delegates the final precedence (active skill > session >
    admin default) to :func:`marcel_core.harness.turn_router.resolve_turn`.
    The stateful classifier/bump/persist logic lives in
    :func:`_resolve_session_tier`. Slash-prefix parsing happens at the
    channel layer before this function is reached, so ``user_text`` is used
    only for classifier/bump input.

    Returns ``(tier, reason)`` with the same log-friendly reason strings as
    before: ``classified:...``, ``session:...``, ``frustration_bump:...``,
    ``skill:<name>:<tier>``.
    """
    skill_override = _active_skill_tier(user_slug, active_skill_names, role)
    active_skill_tier_val = skill_override[0] if skill_override else None

    session_tier, session_reason = _resolve_session_tier(user_slug, channel, user_text)

    plan = resolve_turn(
        '',
        active_skill_tier=active_skill_tier_val,
        session_tier=session_tier,
        admin_config=AdminTierConfig.from_settings(),
    )

    if plan.source is TierSource.ACTIVE_SKILL and skill_override is not None:
        return plan.tier, f'skill:{skill_override[1]}:{plan.tier.value}'
    return plan.tier, session_reason


async def stream_turn(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    *,
    model: str | Model | None = None,
    cwd: str | None = None,
    turn_plan: TurnPlan | None = None,
) -> AsyncIterator[MarcelEvent]:
    """Stream events from a single conversation turn.

    Creates a stateless agent with context from JSONL history and memories.
    Yields MarcelEvent objects for the channel to handle.

    Args:
        user_slug: The user's slug.
        channel: The originating channel.
        user_text: The user's message for this turn. If ``turn_plan`` is
            supplied, ``turn_plan.cleaned_text`` replaces this for storage,
            context building, and the model prompt — the channel has already
            stripped any slash prefix.
        conversation_id: The active conversation identifier.
        model: Optional model override — a qualified string (e.g.
            ``'openai:gpt-4o'``) or a pydantic-ai ``Model`` instance. An
            instance short-circuits the fallback chain to a single entry
            (the scenario-test seam, ADR-260706-b88015).
        turn_plan: Optional pre-resolved plan from the channel adapter. When
            supplied, its ``tier`` wins over the classifier/session path iff
            ``source`` is ``USER_PREFIX`` (one-shot user override); its
            ``skill_override`` builds that skill's capability **eager**
            (``defer_loading=False``) so its SKILL.md body is in the system
            prompt from the first request.

    Yields:
        MarcelEvent instances: RunStarted, TextDelta, ToolCallStarted,
        ToolCallCompleted, RunFinished.
    """
    role = get_user_role(user_slug)

    # Per-turn lifecycle event bus. Core behaviours (the self-mod path guard,
    # role-gating) subscribe as ``tool_call`` handlers via
    # ``register_core_handlers``; extensions may subscribe too. The bus is
    # attached to ``deps.turn`` below so the tool interception layer
    # (:class:`~marcel_core.capabilities.policy.MarcelPolicy`) can reach it.
    event_bus = EventBus()
    register_core_handlers(event_bus)
    # Replay extension-registered on() subscriptions onto this turn's bus.
    # Imported lazily: marcel_core.plugin.__init__ pulls in the channel
    # registry, which imports this module — a top-level import would cycle.
    from marcel_core.plugin.extension import extension_registry

    extension_registry().apply_to_bus(event_bus)
    event_ctx = EventContext(user_slug=user_slug, role=role, channel=channel)
    await event_bus.emit(SessionStartEvent(), event_ctx)

    # The channel has already stripped any slash prefix — use the cleaned
    # text everywhere downstream (segment append, context query, user prompt).
    effective_text = turn_plan.cleaned_text if turn_plan is not None else user_text
    await event_bus.emit(InputEvent(text=effective_text), event_ctx)

    # For admin users on non-CLI channels, default cwd to the user's home directory.
    # For CLI sessions, cwd comes from the client's current directory.
    effective_cwd = cwd
    if role == 'admin' and not effective_cwd and channel != 'cli':
        effective_cwd = str(Path.home())

    deps = MarcelDeps(
        user_slug=user_slug,
        conversation_id=conversation_id,
        channel=channel,
        model=model,
        role=role,
        cwd=effective_cwd,
    )
    deps.turn.event_bus = event_bus

    # Build context from continuous conversation (handles idle summarization)
    message_history = await build_context(user_slug, channel)

    # ``/<skillname>`` dispatch — force-load the skill's capability
    # (non-deferred, body in the prompt) so the model sees its instructions
    # from the first request, with the user's remaining text as the turn
    # input. Mirrors Claude Code's skills-as-prompt-templates pattern.
    eager_skill = turn_plan.skill_override if turn_plan is not None else None

    # Skills active this conversation (loaded via load_capability + the slash
    # override) drive the tier — a demanding skill (marcel-tier: power) bumps.
    active_skill_names = _loaded_skill_names(message_history, extra={eager_skill} if eager_skill else None)

    # The id stamped on stored messages and passed to the agent run — the
    # persistence store routes on this exact '{user_slug}:{channel}' shape.
    # (The channel-facing ``conversation_id`` param is unchanged in events;
    # the stored field was never read back, so the stamp change is safe.)
    stored_conversation_id = conversation_key(user_slug, channel)

    # Spills from OverflowingToolOutput land in THIS user's paste store;
    # task-scoped, so concurrent turns for different users cannot cross.
    from marcel_core.capabilities.persistence.overflow import current_overflow_user

    current_overflow_user.set(user_slug)

    # Append user message to segment (after loading context, so it's not duplicated)
    user_msg = HistoryMessage(
        role='user',
        text=effective_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=stored_conversation_id,
    )
    append_to_segment(user_slug, channel, user_msg)

    # Build system prompt with context (async version includes AI-selected memories)
    from marcel_core.harness.context import build_instructions_async

    system_prompt = await build_instructions_async(deps, query=effective_text)

    # before_agent_start — handlers may rewrite the system prompt (inject
    # guidance) before the agent is built for any tier.
    start_event = await event_bus.emit(
        BeforeAgentStartEvent(system_prompt=system_prompt),
        event_ctx,
    )
    system_prompt = start_event.system_prompt

    # Tier selection (ISSUE-e0db47, ISSUE-6a38cd): if the channel pre-resolved
    # the turn with a user-prefix override (``/fast`` etc.), honor it; otherwise
    # delegate to the session/classifier path. Subagent ``model:`` overrides
    # resolve separately in delegate.py and never reach this path.
    if turn_plan is not None and turn_plan.source is TierSource.USER_PREFIX:
        tier = turn_plan.tier
        tier_reason = f'user_prefix:{tier.value}'
    else:
        tier, tier_reason = _resolve_turn_tier(user_slug, channel, effective_text, active_skill_names, role)
    log.info(
        'tier_resolved user=%s channel=%s tier=%s reason=%s',
        user_slug,
        channel,
        tier.value,
        tier_reason,
    )

    # Primary model: explicit override > per-channel pin > tier default.
    # ``build_chain`` picks the backup from the tier's env var either way.
    # Admin ``fallback_tier`` controls which tier tails the chain as the
    # cloud-outage explainer (default ``LOCAL`` — historical behavior).
    primary_model = model or load_channel_model(user_slug, channel)
    admin_config = AdminTierConfig.from_settings()
    chain = build_chain(
        tier=tier,
        primary=primary_model,
        mode='explain',
        fallback_tier=admin_config.fallback_tier,
    )

    yield RunStarted(conversation_id=conversation_id)

    assistant_text_parts: list[str] = []
    is_error = False
    total_cost = None
    all_messages: list[ModelMessage] = []

    # Driver loop over the fallback chain (ISSUE-076). Pre-stream failures
    # silently retry against the next tier; mid-stream failures surface as an
    # error tail on whatever was already sent (no retry — by design).
    committed = False
    last_exc: Exception | None = None
    last_category: str = 'permanent'
    current: TierEntry | None = chain[0] if chain else None
    chain_exhausted = False

    while current is not None:
        # Build a tier-specific agent. The explain tier gets a synthesised
        # system prompt, no tools, no message history, and a hard cap of 1
        # request so a small local model cannot accidentally start a tool loop.
        if current.purpose == 'explain':
            tier_system_prompt = build_explain_system_prompt(
                str(last_exc) if last_exc else '(no error recorded)',
                last_category,
            )
            tier_user_prompt = build_explain_user_prompt(effective_text)
            tier_history: list[ModelMessage] = []
            tier_usage_limits = UsageLimits(request_limit=1)
            try:
                tier_agent = create_marcel_agent(
                    current.model,
                    system_prompt=tier_system_prompt,
                    role=role,
                    tool_filter=set(),
                    memory=False,
                    code_mode=False,
                    skills=False,
                )
            except Exception as exc:
                log.warning(
                    '%s-%s: could not build explain-tier agent model=%s: %s',
                    user_slug,
                    channel,
                    current.model,
                    exc,
                )
                last_exc = exc
                chain_exhausted = True
                break
        else:
            tier_user_prompt = effective_text
            tier_history = message_history
            tier_usage_limits = UsageLimits(request_limit=15)
            try:
                tier_agent = create_marcel_agent(
                    current.model,
                    system_prompt=system_prompt,
                    role=role,
                    cwd=effective_cwd,
                    user_slug=user_slug,
                    eager_skill=eager_skill,
                )
            except Exception as exc:
                log.warning(
                    '%s-%s: could not build tier=%s agent model=%s: %s',
                    user_slug,
                    channel,
                    current.tier.value,
                    current.model,
                    exc,
                )
                last_exc = exc
                eligible, last_category = is_fallback_eligible(str(exc))
                if not eligible:
                    chain_exhausted = True
                    break
                nxt = next_tier(chain, current, last_category)
                if nxt is None:
                    chain_exhausted = True
                    break
                current = nxt
                continue

        await event_bus.emit(
            BeforeProviderRequestEvent(model=model_label(current.model), tier=current.tier.value),
            event_ctx,
        )
        try:
            async with tier_agent.run_stream(
                tier_user_prompt,
                deps=deps,
                message_history=tier_history,
                usage_limits=tier_usage_limits,
                # StepPersistence routes on this; the explain tier passes None
                # so its out-of-band run never touches conversation storage.
                conversation_id=None if current.purpose == 'explain' else stored_conversation_id,
            ) as result:
                log.info(
                    '%s-%s: stream started tier=%s model=%s',
                    user_slug,
                    channel,
                    current.tier.value,
                    model_label(current.model),
                )
                async for text_delta in result.stream_text(delta=True, debounce_by=0.01):
                    if text_delta:
                        if not committed:
                            committed = True
                        yield TextDelta(text=text_delta)
                        assistant_text_parts.append(text_delta)

                # Wait for full completion (runs on_complete, processes trailing tool calls)
                await result.get_output()
                log.debug('%s-%s: stream finished tier=%s', user_slug, channel, current.tier.value)

                # Capture only THIS run's messages for tool-call extraction.
                # all_messages() would include the converted history prefix,
                # and _extract_tool_history walks every message — historical
                # tool entries would be re-appended to the segment each turn,
                # compounding (STORY-260718-bfb1ac).
                all_messages = result.new_messages()

                usage = result.usage
                if usage and usage.total_tokens:
                    log.info(
                        '%s-%s: turn complete tier=%s — %d tokens (in: %d, out: %d, requests: %d)',
                        user_slug,
                        channel,
                        current.tier.value,
                        usage.total_tokens,
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.requests,
                    )
            # Successful run — break out of the chain loop.
            break

        except Exception as exc:
            last_exc = exc
            eligible, last_category = is_fallback_eligible(str(exc))

            if committed:
                # Mid-stream failure — keep the partial output, append an
                # error tail, don't retry. Retrying would either duplicate
                # work on tier 2 or discard output the user already saw.
                log.warning(
                    '%s-%s: mid-stream failure on tier=%s: %s',
                    user_slug,
                    channel,
                    current.tier.value,
                    exc,
                )
                is_error = True
                error_tail = f'\n\n[Error mid-response: {exc}]'
                yield TextDelta(text=error_tail)
                assistant_text_parts.append(error_tail)
                break

            if not eligible:
                log.warning(
                    '%s-%s: permanent error on tier=%s: %s',
                    user_slug,
                    channel,
                    current.tier.value,
                    exc,
                )
                is_error = True
                error_text = f'Error: {exc}'
                yield TextDelta(text=error_text)
                assistant_text_parts.append(error_text)
                break

            log.info(
                '%s-%s: tier=%s failed (%s) — advancing',
                user_slug,
                channel,
                current.tier.value,
                last_category,
            )
            # Pre-stream failure on an eligible category. Nothing has been
            # yielded yet; drop any empty accumulator state and advance.
            assistant_text_parts.clear()
            nxt = next_tier(chain, current, last_category)
            if nxt is None:
                chain_exhausted = True
                break
            current = nxt
            continue

    if chain_exhausted and not committed and not is_error:
        # Either the chain was empty (impossible — build_chain always returns
        # at least tier 1), or every tier failed pre-stream and the loop
        # couldn't even build the explain agent. Surface a clean error.
        is_error = True
        error_text = (
            f'Error: all model tiers failed. Last error: {last_exc}'
            if last_exc is not None
            else 'Error: no model tiers available for this request'
        )
        yield TextDelta(text=error_text)
        assistant_text_parts.append(error_text)

    # Emit channel events for the turn's tool calls. Persistence of these
    # entries is the StepPersistence store's job now (flushed on
    # run_completed, so a failed tier attempt persists nothing) — this loop
    # only narrates progress to the channel. The extraction is repeated here
    # rather than shared with the store because the store flushes mid-run,
    # before this code observes the result.
    if all_messages:
        tool_entries = extract_tool_history(all_messages, user_slug, stored_conversation_id)
        for entry in tool_entries:
            # Yield events for tool calls so channels can show progress
            if entry.role == 'assistant' and entry.tool_calls:
                for tc in entry.tool_calls:
                    yield ToolCallStarted(tool_call_id=tc.id, tool_name=tc.name)
            elif entry.role == 'tool':
                yield ToolCallCompleted(
                    tool_call_id=entry.tool_call_id or '',
                    tool_name=entry.tool_name or '',
                    result=entry.text or '',
                    is_error=entry.is_error,
                )

    # Save final assistant text response to segment
    assistant_text = ''.join(assistant_text_parts)

    # agent_end — the turn's response is complete (observe-only in F0).
    await event_bus.emit(AgentEndEvent(response_text=assistant_text), event_ctx)

    if assistant_text:
        assistant_msg = HistoryMessage(
            role='assistant',
            text=assistant_text,
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id=stored_conversation_id,
        )
        append_to_segment(user_slug, channel, assistant_msg)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
