"""Agent runner — streams events from pydantic-ai for one conversation turn.

Creates a stateless agent per turn, building context from segment-based
conversation history and dynamically selected memories.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from marcel_core.config import settings
from marcel_core.harness.agent import create_marcel_agent
from marcel_core.harness.context import MarcelDeps
from marcel_core.harness.model_chain import (
    Tier,
    TierEntry,
    build_chain,
    build_explain_system_prompt,
    build_explain_user_prompt,
    is_fallback_eligible,
    next_tier,
)
from marcel_core.harness.tier_classifier import (
    classify_initial_tier,
    load_routing_config,
    maybe_bump_tier,
)
from marcel_core.memory.conversation import (
    MAX_SUMMARY_CHARS,
    append_to_segment,
    load_latest_summary,
    read_active_segment,
)
from marcel_core.memory.history import HistoryMessage, ToolCall
from marcel_core.memory.pastes import PASTE_THRESHOLD, store_paste
from marcel_core.memory.summarizer import summarize_if_idle
from marcel_core.storage.settings import (
    load_channel_model,
    load_channel_tier,
    save_channel_tier,
)
from marcel_core.storage.users import get_user_role

log = logging.getLogger(__name__)

# Tool result preview length for the previous turn
_TOOL_RESULT_PREVIEW_LEN = 200

# Tools whose results should always be kept in full (regardless of age)
_ALWAYS_KEEP_TOOLS = frozenset({'marcel'})

# Aggressive tool lifecycle: only current turn (0) and previous turn (1).
_FULL_RESULT_TURNS = 1  # turn 0 = current
_PREVIEW_RESULT_TURNS = 2  # turn 1 = previous


def _tool_result_for_context(
    text: str | None,
    tool_name: str | None,
    turn_age: int,
) -> str:
    """Apply aggressive tool result lifecycle based on turn age.

    - Current turn (age 0): full result
    - Previous turn (age 1): 200-char preview
    - Older (age 2+): inline name-only note

    Args:
        text: The tool result content.
        tool_name: The tool that produced this result.
        turn_age: How many turns ago this result was produced (0 = current turn).

    Returns:
        The (possibly trimmed) result string for inclusion in context.
    """
    if not text:
        return f'({tool_name or "tool"} completed with no output)'

    # Always keep results for certain tools
    if tool_name and tool_name in _ALWAYS_KEEP_TOOLS:
        return text

    # Current turn: full result
    if turn_age < _FULL_RESULT_TURNS:
        return text

    # Previous turn: short preview
    if turn_age < _PREVIEW_RESULT_TURNS:
        if len(text) > _TOOL_RESULT_PREVIEW_LEN:
            return text[:_TOOL_RESULT_PREVIEW_LEN] + f'\n... ({len(text)} chars total, truncated)'
        return text

    # Older turns: name-only note
    return f'[Used {tool_name or "tool"}]'


def _messages_to_model(
    messages: list[HistoryMessage],
    num_turns: int | None = None,
) -> list[ModelMessage]:
    """Convert internal HistoryMessage objects to pydantic-ai ModelMessage format.

    Handles user, assistant (with tool calls), and tool result messages.
    Applies aggressive tool lifecycle trimming based on turn age.

    Args:
        messages: The history messages to convert.
        num_turns: Total number of turns for age calculation.
                   If None, count from the messages themselves.
    """
    # Count turns (user messages) to compute age for tiered trimming
    turn_count = sum(1 for m in messages if m.role == 'user') if num_turns is None else num_turns
    current_turn = 0

    result: list[ModelMessage] = []
    # Collect consecutive tool-result messages into a single ModelRequest
    pending_tool_returns: list[ToolReturnPart] = []

    def _flush_tool_returns() -> None:
        if pending_tool_returns:
            result.append(ModelRequest(parts=list(pending_tool_returns)))
            pending_tool_returns.clear()

    for msg in messages:
        if msg.role == 'user':
            _flush_tool_returns()
            current_turn += 1
            if not msg.text:
                continue
            result.append(ModelRequest(parts=[UserPromptPart(content=msg.text, timestamp=msg.timestamp)]))

        elif msg.role == 'assistant':
            _flush_tool_returns()
            parts: list[TextPart | ToolCallPart] = []
            if msg.text:
                parts.append(TextPart(content=msg.text))
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(
                        ToolCallPart(
                            tool_name=tc.name,
                            args=tc.arguments,
                            tool_call_id=tc.id,
                        )
                    )
            if parts:
                result.append(ModelResponse(parts=parts, timestamp=msg.timestamp))

        elif msg.role == 'tool':
            turn_age = turn_count - current_turn
            content = _tool_result_for_context(msg.text, msg.tool_name, turn_age)
            pending_tool_returns.append(
                ToolReturnPart(
                    tool_name=msg.tool_name or 'unknown',
                    content=content,
                    tool_call_id=msg.tool_call_id or '',
                    outcome='failed' if msg.is_error else 'success',
                    timestamp=msg.timestamp,
                )
            )

        elif msg.role == 'system':
            _flush_tool_returns()
            if msg.text:
                result.append(ModelRequest(parts=[UserPromptPart(content=msg.text, timestamp=msg.timestamp)]))

    _flush_tool_returns()
    return result


async def build_context(
    user_slug: str,
    channel: str,
) -> list[ModelMessage]:
    """Build the context window for a conversation turn.

    Loads the rolling summary (if any) and active segment messages,
    applies tool lifecycle trimming, and returns ModelMessage list.

    1. Check for idle summarization (seals segment if idle >1 hour)
    2. Load latest summary from sealed segments
    3. Load active segment messages
    4. Apply tool result lifecycle
    5. Prepend summary as context
    """
    # 1. Check for idle summarization before building context
    idle_minutes = settings.marcel_idle_summarize_minutes
    summarized = await summarize_if_idle(user_slug, channel, idle_minutes)
    if summarized:
        log.info('%s-%s: idle summarization completed before turn', user_slug, channel)
        # ISSUE-e0db47: session boundary → clear the tier so the next
        # message re-classifies from scratch instead of inheriting the
        # prior session's tier.
        from marcel_core.storage.settings import clear_channel_tier

        clear_channel_tier(user_slug, channel)

    # 2. Load latest summary
    latest_summary = load_latest_summary(user_slug, channel)

    # 3. Load active segment messages
    active_messages = read_active_segment(user_slug, channel)

    # 4. Convert to model messages with tool lifecycle applied
    model_messages = _messages_to_model(active_messages)

    # 5. Prepend summary as context if it exists
    if latest_summary:
        summary_text = latest_summary.summary
        # Cap summary to avoid blowing the token budget
        if len(summary_text) > MAX_SUMMARY_CHARS:
            summary_text = summary_text[:MAX_SUMMARY_CHARS] + '\n... (summary truncated)'
        summary_msg = ModelRequest(parts=[UserPromptPart(content=f'[Previous conversation summary: {summary_text}]')])
        model_messages.insert(0, summary_msg)

    return model_messages


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


def _prime_read_skills_from_history(messages: Sequence[ModelMessage], read_skills: set[str]) -> None:
    """Populate ``read_skills`` from past ``marcel(read_skill, name=X)`` calls.

    Scans the message history for any assistant tool call invoking the
    ``marcel`` tool with ``action='read_skill'`` and adds the requested
    skill name to ``read_skills``. Since ``marcel`` tool results are
    always kept in full across turns (see :data:`_ALWAYS_KEEP_TOOLS`),
    the docs for any skill loaded this way are guaranteed to still be in
    the model's context — so the integration tool's auto-load does not
    need to re-inject them on subsequent turns.
    """
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart) or part.tool_name != 'marcel':
                continue
            args = part.args_as_dict() if callable(getattr(part, 'args_as_dict', None)) else part.args
            if not isinstance(args, dict):
                continue
            if args.get('action') != 'read_skill':
                continue
            name = args.get('name')
            if isinstance(name, str) and name:
                read_skills.add(name)


def _extract_tool_history(
    all_messages: list[ModelMessage],
    user_slug: str,
    conversation_id: str,
) -> list[HistoryMessage]:
    """Extract tool call and result history from pydantic-ai messages.

    Walks the message list produced by ``result.all_messages()`` and converts
    tool-related parts into HistoryMessage entries for JSONL storage.
    Large tool results are offloaded to the paste store.

    Returns assistant messages with tool_calls and tool-role result messages.
    Skips the initial user prompt and final text-only response (handled by caller).
    """
    entries: list[HistoryMessage] = []
    now = datetime.now(tz=timezone.utc)

    for msg in all_messages:
        if isinstance(msg, ModelResponse):
            tool_calls = msg.tool_calls
            if not tool_calls:
                continue
            # Build HistoryMessage for assistant with tool calls
            tc_list = [
                ToolCall(
                    id=tc.tool_call_id,
                    name=tc.tool_name,
                    arguments=tc.args_as_dict()
                    if callable(getattr(tc, 'args_as_dict', None))
                    else (tc.args if isinstance(tc.args, dict) else {}),
                )
                for tc in tool_calls
            ]
            # Collect any text parts in this response
            text_parts = [p.content for p in msg.parts if isinstance(p, TextPart) and p.content]
            entries.append(
                HistoryMessage(
                    role='assistant',
                    text='\n'.join(text_parts) if text_parts else None,
                    timestamp=msg.timestamp or now,
                    conversation_id=conversation_id,
                    tool_calls=tc_list,
                )
            )

        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    # Serialize content to string
                    content = _serialize_tool_content(part.content)
                    # Offload large results to paste store
                    result_ref = None
                    if len(content) >= PASTE_THRESHOLD:
                        result_ref = store_paste(user_slug, content)
                        # Keep a preview in text for scanning
                        content = content[:_TOOL_RESULT_PREVIEW_LEN]

                    entries.append(
                        HistoryMessage(
                            role='tool',
                            text=content,
                            timestamp=part.timestamp or now,
                            conversation_id=conversation_id,
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            result_ref=result_ref,
                            is_error=part.outcome == 'failed',
                        )
                    )
                elif isinstance(part, RetryPromptPart):
                    error_text = (
                        part.content if isinstance(part.content, str) else json.dumps(part.content, default=str)
                    )
                    entries.append(
                        HistoryMessage(
                            role='tool',
                            text=error_text,
                            timestamp=part.timestamp or now,
                            conversation_id=conversation_id,
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            is_error=True,
                        )
                    )

    return entries


def _serialize_tool_content(content: object) -> str:
    """Convert tool return content to a string for storage."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


_TIER_RANK = {'fast': 1, 'standard': 2, 'power': 3}
_TIER_FROM_STR = {'fast': Tier.FAST, 'standard': Tier.STANDARD, 'power': Tier.POWER}


def _active_skill_tier(user_slug: str, active_names: set[str]) -> tuple[Tier, str] | None:
    """Highest ``preferred_tier`` among skills whose docs are in this turn's context.

    POWER beats STANDARD beats FAST — a demanding skill wins. Returns
    ``(tier, skill_name)`` or ``None`` when no active skill declares a tier.
    """
    if not active_names:
        return None
    from marcel_core.skills.loader import load_skills

    best: tuple[Tier, str] | None = None
    best_rank = 0
    for doc in load_skills(user_slug):
        if doc.name not in active_names or not doc.preferred_tier:
            continue
        rank = _TIER_RANK.get(doc.preferred_tier, 0)
        if rank > best_rank:
            best_rank = rank
            best = (_TIER_FROM_STR[doc.preferred_tier], doc.name)
    return best


def _resolve_turn_tier(
    user_slug: str,
    channel: str,
    user_text: str,
    active_skill_names: set[str],
) -> tuple[Tier, str]:
    """Decide which tier this interactive turn runs on.

    Precedence (highest wins):

    1. Active skill ``preferred_tier`` — per-turn override, does **not**
       mutate the session tier. When multiple active skills declare a tier,
       the highest one wins.
    2. Session tier in ``channel_tiers``. Set by the classifier on the
       first message of the session and bumped on frustration.
    3. Classifier on the first message — saves the result to
       ``channel_tiers``. The classifier only picks FAST or STANDARD;
       POWER is never auto-selected here.

    Frustration detection runs only when falling through to the session
    tier (step 2/3). A bump (FAST → STANDARD) mutates ``channel_tiers``.

    Returns ``(tier, reason)`` where ``reason`` is a short log-friendly
    string identifying why this tier was picked.
    """
    skill_override = _active_skill_tier(user_slug, active_skill_names)
    if skill_override is not None:
        tier, name = skill_override
        return tier, f'skill:{name}:{tier.value}'

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


async def stream_turn(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
) -> AsyncIterator[MarcelEvent]:
    """Stream events from a single conversation turn.

    Creates a stateless agent with context from JSONL history and memories.
    Yields MarcelEvent objects for the channel to handle.

    Args:
        user_slug: The user's slug.
        channel: The originating channel.
        user_text: The user's message for this turn.
        conversation_id: The active conversation identifier.
        model: Optional model override (e.g., 'openai:gpt-4').

    Yields:
        MarcelEvent instances: RunStarted, TextDelta, ToolCallStarted,
        ToolCallCompleted, RunFinished.
    """
    role = get_user_role(user_slug)

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

    # Build context from continuous conversation (handles idle summarization)
    message_history = await build_context(user_slug, channel)

    # Prime per-turn read_skills from history so the integration tool's
    # auto-load doesn't re-inject docs that are already visible to the model.
    _prime_read_skills_from_history(message_history, deps.turn.read_skills)

    # Append user message to segment (after loading context, so it's not duplicated)
    user_msg = HistoryMessage(
        role='user',
        text=user_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_to_segment(user_slug, channel, user_msg)

    # Build system prompt with context (async version includes AI-selected memories)
    from marcel_core.harness.context import build_instructions_async

    system_prompt = await build_instructions_async(deps, query=user_text)

    # Tier selection (ISSUE-e0db47): active skill preferred_tier > session tier
    # (set by classifier, bumped on frustration). Subagent ``model:`` overrides
    # resolve separately in delegate.py and never reach this path.
    tier, tier_reason = _resolve_turn_tier(user_slug, channel, user_text, deps.turn.read_skills)
    log.info(
        'tier_resolved user=%s channel=%s tier=%s reason=%s',
        user_slug,
        channel,
        tier.value,
        tier_reason,
    )

    # Primary model: explicit override > per-channel pin > tier default.
    # ``build_chain`` picks the backup from the tier's env var either way.
    primary_model = model or load_channel_model(user_slug, channel)
    chain = build_chain(tier=tier, primary=primary_model, mode='explain')

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
            tier_user_prompt = build_explain_user_prompt(user_text)
            tier_history: list[ModelMessage] = []
            tier_usage_limits = UsageLimits(request_limit=1)
            try:
                tier_agent = create_marcel_agent(
                    current.model,
                    system_prompt=tier_system_prompt,
                    role=role,
                    tool_filter=set(),
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
            tier_user_prompt = user_text
            tier_history = message_history
            tier_usage_limits = UsageLimits(request_limit=15)
            try:
                tier_agent = create_marcel_agent(
                    current.model,
                    system_prompt=system_prompt,
                    role=role,
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

        try:
            async with tier_agent.run_stream(
                tier_user_prompt,
                deps=deps,
                message_history=tier_history,
                usage_limits=tier_usage_limits,
            ) as result:
                log.info(
                    '%s-%s: stream started tier=%s model=%s',
                    user_slug,
                    channel,
                    current.tier.value,
                    current.model,
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

                # Capture all messages for tool call extraction
                all_messages = result.all_messages()

                usage = result.usage()
                if usage and usage.total_tokens:
                    log.info(
                        '%s-%s: turn complete tier=%s — %d tokens (in: %d, out: %d, requests: %d)',
                        user_slug,
                        channel,
                        current.tier.value,
                        usage.total_tokens,
                        usage.request_tokens,
                        usage.response_tokens,
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

    # Extract and save tool call history from the pydantic-ai message trace.
    # This captures intermediate tool calls (assistant→tool→assistant loops)
    # that happen during a single turn, before the final text response.
    if all_messages:
        tool_entries = _extract_tool_history(all_messages, user_slug, conversation_id)
        for entry in tool_entries:
            append_to_segment(user_slug, channel, entry)
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
    if assistant_text:
        assistant_msg = HistoryMessage(
            role='assistant',
            text=assistant_text,
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id=conversation_id,
        )
        append_to_segment(user_slug, channel, assistant_msg)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
