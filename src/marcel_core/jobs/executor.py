"""Job executor — runs a job definition as a headless Marcel agent turn.

The executor:
1. Creates a MarcelDeps context for the job's user
2. Builds a system prompt from the job definition + user profile
3. Creates a Marcel agent with the job's model and tools
4. Runs the agent with the job's task message (non-streaming)
5. Captures output, logs the run, sends notifications
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from marcel_core.harness.model_chain import FALLBACK_ELIGIBLE_CATEGORIES, classify_error
from marcel_core.jobs import append_run
from marcel_core.jobs.models import JobDefinition, JobRun, NotifyPolicy, RunStatus

log = logging.getLogger(__name__)

# ``FALLBACK_ELIGIBLE_CATEGORIES`` and ``classify_error`` are re-exported from
# :mod:`marcel_core.harness.model_chain` — they used to live here, but the
# post-076 audit (ISSUE-077) moved them next to ``build_chain`` / ``next_tier``
# where the fallback policy is defined. Keeping the symbols visible in this
# module's namespace preserves ``from marcel_core.jobs.executor import
# classify_error`` for the existing tests that spell it that way.
__all__ = ['FALLBACK_ELIGIBLE_CATEGORIES', 'classify_error']


def _load_job_memories(user_slug: str) -> str:
    """Load preference and feedback memories for injection into a job agent.

    Jobs don't get full memory selection (no query to match against), but
    they should still respect user preferences and behavioral feedback.
    Returns a formatted ``## User preferences`` section, or empty string.
    """
    from marcel_core.storage.memory import MemoryType, load_memory_file, scan_memory_headers

    headers = scan_memory_headers(user_slug)
    relevant = [h for h in headers if h.type in (MemoryType.PREFERENCE, MemoryType.FEEDBACK)]
    if not relevant:
        return ''

    blocks: list[str] = []
    for header in relevant:
        topic = header.filename.removesuffix('.md')
        content = load_memory_file(user_slug, topic)
        if not content.strip():
            continue
        label = header.name or topic.replace('_', ' ')
        tag = f'[{header.type.value}]' if header.type else ''
        blocks.append(f'### {tag} {label}\n{content.strip()}')

    if not blocks:
        return ''

    return '## User preferences & feedback\n\n' + '\n\n'.join(blocks)


def _resolve_job_skills(job: JobDefinition) -> list:
    """Load full SkillDoc objects for skills referenced by a job.

    Job skills may use integration IDs like ``"icloud.calendar"`` — the skill
    name is the part before the dot (or the whole string if no dot).
    """
    from marcel_core.skills.loader import load_skills

    all_skills = load_skills(job.user_slug)
    skill_map = {s.name: s for s in all_skills}

    # Extract unique skill names from job.skills (e.g. "icloud.calendar" -> "icloud")
    wanted: set[str] = set()
    for ref in job.skills:
        wanted.add(ref.split('.')[0])

    return [skill_map[name] for name in sorted(wanted) if name in skill_map]


def _build_job_context(job: JobDefinition) -> str:
    """Build the system prompt context for a job agent.

    Assembles: job system prompt + skill docs + credentials + channel prompt.
    Deliberately lean — no MARCEL.md, skill index, or memory selection.
    """
    from marcel_core.harness.context import load_channel_prompt
    from marcel_core.storage.credentials import load_credentials

    parts = [job.system_prompt]

    # Auto-inject full docs for referenced skills
    skills = _resolve_job_skills(job)
    if skills:
        skill_sections = []
        for skill in skills:
            if not skill.is_setup:
                skill_sections.append(f'### {skill.name}\n\n{skill.content}')
        if skill_sections:
            parts.append('## Skill reference\n\n' + '\n\n---\n\n'.join(skill_sections))

    # Inject credentials: from skill requirements + any referenced in system_prompt
    cred_keys: set[str] = set()
    for skill in skills:
        cred_keys.update(skill.credential_keys)

    # Also check vault for keys mentioned literally in the job text
    # (covers credentials not tied to a skill, e.g. DETIJD_ in the scraper prompt)
    all_creds = load_credentials(job.user_slug)
    job_text = job.system_prompt + ' ' + job.task
    for key in all_creds:
        if key in job_text:
            cred_keys.add(key)

    relevant = {k: all_creds[k] for k in sorted(cred_keys) if k in all_creds}
    if relevant:
        lines = ['## Credentials (injected from vault)']
        for key, value in sorted(relevant.items()):
            lines.append(f'- **{key}**: `{value}`')
        parts.append('\n'.join(lines))

    # Inject preference + feedback memories so jobs adapt to user behavior
    memory_section = _load_job_memories(job.user_slug)
    if memory_section:
        parts.append(memory_section)

    # Channel delivery guidance
    channel_prompt = load_channel_prompt('job')
    parts.append(f'## Channel\n{channel_prompt}')

    # Delivery policy — the `notify` field is the single source of truth for
    # whether this run is allowed to send a user-visible message. The agent
    # reads this block to decide whether to call marcel(action="notify").
    parts.append(_format_delivery_policy(job.notify))

    return '\n\n---\n\n'.join(parts)


_DELIVERY_POLICY_TEXT: dict[NotifyPolicy, str] = {
    NotifyPolicy.SILENT: (
        'This job is **silent**. Do NOT call `marcel(action="notify")` — '
        'notifications are suppressed and will not reach the user. Return your '
        'result as normal tool output; it will be logged for inspection only.'
    ),
    NotifyPolicy.ON_FAILURE: (
        'This job only alerts the user on failure. Do NOT call '
        '`marcel(action="notify")` on a successful run — notifications are '
        'suppressed. Return your result as normal tool output; the scheduler '
        'will send an alert if the run fails or errors out.'
    ),
    NotifyPolicy.ON_OUTPUT: (
        'This job delivers its output to the user automatically. You MAY call '
        '`marcel(action="notify", message="...")` if you want to compose a '
        'richer user-facing message — otherwise just return the result as '
        'output and the scheduler will deliver it. Do not do both.'
    ),
    NotifyPolicy.ALWAYS: (
        'This job always delivers a message to the user. Call '
        '`marcel(action="notify", message="...")` with the full user-facing '
        'message, OR return it as normal output — the scheduler will deliver '
        'whatever is produced. Do not do both.'
    ),
}


def _format_delivery_policy(policy: NotifyPolicy) -> str:
    """Render the ``## Delivery policy`` block for a job's system prompt."""
    body = _DELIVERY_POLICY_TEXT.get(policy, _DELIVERY_POLICY_TEXT[NotifyPolicy.ON_OUTPUT])
    return f'## Delivery policy\n{body}'


async def execute_job(job: JobDefinition, trigger_reason: str = 'scheduled') -> JobRun:
    """Execute a single job and return the run record."""
    from marcel_core.harness.agent import create_marcel_agent
    from marcel_core.harness.context import MarcelDeps

    run = JobRun(
        job_id=job.id,
        trigger_reason=trigger_reason,
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    deps = MarcelDeps(
        user_slug=job.user_slug,
        conversation_id=f'job:{job.id}:{run.run_id}',
        channel='job',
        model=job.model,
        role='user',
    )

    # Policy acts as the single source of truth for delivery. Silent and
    # on-failure jobs must not send user-visible messages on success, so
    # agent-initiated notify calls are dropped at the tool layer.
    deps.turn.suppress_notify = job.notify in (NotifyPolicy.SILENT, NotifyPolicy.ON_FAILURE)

    # Prime ``turn.read_skills`` with the skills we're about to inject into
    # the system prompt, so the integration tool's auto-loader doesn't
    # prepend the full SkillDoc to every tool result (the ISSUE-071 fix
    # applied to the job path — the runner primes from history, but jobs
    # have no history, so we seed from the job definition directly).
    for skill in _resolve_job_skills(job):
        deps.turn.read_skills.add(skill.name)

    # Build lean system prompt: task + skill docs + credentials + channel
    system_prompt = _build_job_context(job)

    agent = create_marcel_agent(job.model, system_prompt=system_prompt, role='user')

    # Apply usage limits if configured on the job
    usage_limits = None
    if job.request_limit is not None:
        from pydantic_ai.usage import UsageLimits

        usage_limits = UsageLimits(request_limit=job.request_limit)

    try:
        result = await asyncio.wait_for(
            agent.run(job.task, deps=deps, usage_limits=usage_limits),
            timeout=job.timeout_seconds,
        )
        run.output = result.output
        run.status = RunStatus.COMPLETED
        run.agent_notified = deps.turn.notified
    except asyncio.TimeoutError:
        log.warning('%s-job: job %s (%s) timed out after %ds', job.user_slug, job.id, job.name, job.timeout_seconds)
        run.error = f'Job timed out after {job.timeout_seconds}s'
        run.error_category = 'timeout'
        run.status = RunStatus.TIMED_OUT
    except Exception as exc:
        log.exception('%s-job: job %s (%s) failed', job.user_slug, job.id, job.name)
        run.error = str(exc)
        is_transient, category = classify_error(str(exc))
        run.error_category = category
        run.status = RunStatus.FAILED

    run.finished_at = datetime.now(UTC)
    append_run(job.user_slug, job.id, run)
    return run


async def _run_with_backoff(job: JobDefinition, trigger_reason: str) -> JobRun:
    """Run ``execute_job`` and retry transient failures with exponential backoff.

    Uses ``job.backoff_schedule`` and only retries categories classified as
    transient (``rate_limit``, ``timeout``, ``network``, ``server_error``).
    Permanent and auth-or-quota errors break out immediately — those are
    handled by the caller's tier-advancement logic rather than by more
    same-tier retries.

    Does NOT touch ``job.model`` — the caller is expected to have set it to
    the tier under attempt before calling this helper.
    """
    run = await execute_job(job, trigger_reason)

    attempt = 0
    while run.status in (RunStatus.FAILED, RunStatus.TIMED_OUT) and attempt < job.max_retries:
        # Only retry transient errors
        is_transient = run.error_category in ('rate_limit', 'timeout', 'network', 'server_error')
        if not is_transient:
            log.info('%s-job: permanent error for %s, skipping retries', job.user_slug, job.id)
            break

        attempt += 1
        delay = job.backoff_schedule[min(attempt - 1, len(job.backoff_schedule) - 1)]
        log.info(
            '%s-job: retrying job %s (%s) attempt %d/%d (backoff %ds)',
            job.user_slug,
            job.id,
            job.name,
            attempt,
            job.max_retries,
            delay,
        )
        await asyncio.sleep(delay)
        run = await execute_job(job, trigger_reason)
        run.retry_count = attempt

    return run


async def _execute_chain(job: JobDefinition, trigger_reason: str) -> JobRun:
    """Run a job through the ISSUE-076 fallback chain.

    Builds the chain with ``mode='complete'`` (tier 3 tries to complete the
    task against the local model, preserving ISSUE-070 semantics) and drives
    it with per-tier retry budgets via :func:`_run_with_backoff`.

    Legacy bridge: when ``job.allow_local_fallback=True`` and neither
    ``MARCEL_FALLBACK_MODEL`` nor a chain-resolved tier 3 exists, synthesize
    a ``local:<MARCEL_LOCAL_LLM_MODEL>`` entry so the pre-ISSUE-076 path
    keeps working without requiring users to also set a new env var.
    """
    from marcel_core.config import settings
    from marcel_core.harness.model_chain import Tier, build_chain, is_fallback_eligible, next_tier

    chain = build_chain(primary=job.model, mode='complete')

    has_local_tier = any(e.purpose == 'complete' and e.model.startswith('local:') for e in chain)
    if not job.allow_local_fallback:
        # Strip any local tier 3 but keep cloud tier 2 — confirmed ISSUE-076 decision.
        chain = [e for e in chain if not (e.purpose == 'complete' and e.model.startswith('local:'))]
    elif not has_local_tier and settings.marcel_local_llm_url and settings.marcel_local_llm_model:
        # Legacy ISSUE-070 bridge: no MARCEL_FALLBACK_MODEL set but the job
        # opted into local fallback and MARCEL_LOCAL_LLM_* are configured.
        from marcel_core.harness.model_chain import TierEntry

        chain.append(
            TierEntry(
                tier=Tier.FALLBACK,
                model=f'local:{settings.marcel_local_llm_model}',
                purpose='complete',
            )
        )

    original_model = job.model
    current: TierEntry | None = chain[0] if chain else None
    run: JobRun | None = None

    while current is not None:
        if current.tier != Tier.STANDARD:
            log.info(
                '%s-job: chain advancing to tier=%s model=%s for %s (%s)',
                job.user_slug,
                current.tier.value,
                current.model,
                job.id,
                job.name,
            )
        job.model = current.model
        try:
            run = await _run_with_backoff(job, trigger_reason)
        finally:
            # Ensure the persisted job definition is never mutated with a
            # tier-override model string — restore on every iteration.
            job.model = original_model

        if run.status == RunStatus.COMPLETED:
            if current.tier != Tier.STANDARD:
                # Report the legacy 'local' value when the tier 3 model was
                # a local:<tag> entry, so old runs.jsonl readers stay happy.
                run.fallback_used = 'local' if current.model.startswith('local:') else current.tier.value
            break

        eligible, category = is_fallback_eligible(run.error or '')
        if not eligible:
            break

        nxt = next_tier(chain, current, category)
        if nxt is None:
            # Mark the failed run with the last tier attempted if we escalated
            # at least once, so the persisted run still records the fallback.
            if current.tier != Tier.STANDARD:
                run.fallback_used = 'local' if current.model.startswith('local:') else current.tier.value
            break
        current = nxt

    assert run is not None  # build_chain always returns at least tier 1
    return run


async def execute_job_with_retries(job: JobDefinition, trigger_reason: str = 'scheduled') -> JobRun:
    """Execute a job, retrying on transient failure and chaining on outage.

    Flow (ISSUE-076):

    1. Per-tier exponential backoff via :func:`_run_with_backoff` — retries
       transient errors on the same model.
    2. When ``job.allow_fallback_chain`` is True (the default), escalate
       through the global model chain (``MARCEL_BACKUP_MODEL``, then
       ``MARCEL_FALLBACK_MODEL`` if ``allow_local_fallback`` also permits
       running on a local model for completion).
    3. When ``job.allow_fallback_chain`` is False, pin to ``job.model`` only —
       no cross-provider backup — but still honour the legacy ISSUE-070
       local-LLM fallback if ``allow_local_fallback`` is set.

    Post-run bookkeeping (consecutive errors, notifications, run persistence)
    is shared between both paths.
    """
    from marcel_core.jobs import save_job

    if job.allow_fallback_chain:
        run = await _execute_chain(job, trigger_reason)
    else:
        run = await _execute_pinned_with_legacy_fallback(job, trigger_reason)

    # Update consecutive error tracking on the job definition
    if run.status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
        job.consecutive_errors += 1
        job.last_error_at = datetime.now(UTC)
    else:
        job.consecutive_errors = 0
        job.last_error_at = None
        job.last_failure_alert_at = None
    save_job(job)

    # Notify based on policy + deliver tracking
    delivery_status, delivery_error = await _notify_if_needed(job, run)
    run.delivery_status = delivery_status
    run.delivery_error = delivery_error
    append_run(job.user_slug, job.id, run)

    return run


async def _execute_pinned_with_legacy_fallback(
    job: JobDefinition,
    trigger_reason: str,
) -> JobRun:
    """Execute a job pinned to its configured model (``allow_fallback_chain=False``).

    Preserves the pre-ISSUE-076 behaviour exactly: retry loop on the same
    model, then if ``allow_local_fallback`` is set and the final error is
    fallback-eligible, one local-LLM attempt (ISSUE-070). The chain helper
    is deliberately NOT used — this path exists so users can opt out of
    cross-provider escalation for deterministic or cost-sensitive jobs.
    """
    from marcel_core.config import settings

    run = await _run_with_backoff(job, trigger_reason)

    if (
        run.status in (RunStatus.FAILED, RunStatus.TIMED_OUT)
        and job.allow_local_fallback
        and settings.marcel_local_llm_url
        and settings.marcel_local_llm_model
        and run.error_category in FALLBACK_ELIGIBLE_CATEGORIES
    ):
        original_model = job.model
        fallback_model = f'local:{settings.marcel_local_llm_model}'
        log.info(
            '%s-job: local fallback firing for %s (%s) — category=%s cloud=%s local=%s',
            job.user_slug,
            job.id,
            job.name,
            run.error_category,
            original_model,
            fallback_model,
        )
        job.model = fallback_model
        try:
            fb_run = await execute_job(job, trigger_reason)
        finally:
            job.model = original_model
        fb_run.retry_count = run.retry_count
        fb_run.fallback_used = 'local'
        run = fb_run

    return run


async def _notify_if_needed(job: JobDefinition, run: JobRun) -> tuple[str, str | None]:
    """Send a notification to the user based on the job's notify policy.

    Returns ``(delivery_status, delivery_error)`` where status is one of
    ``"sent"``, ``"failed"``, or ``"skipped"``.
    """
    from marcel_core.jobs import save_job

    # If the agent already sent a notification during the run (via marcel(action="notify")),
    # skip the executor's automatic notification to avoid double-sending.
    if run.agent_notified and run.status == RunStatus.COMPLETED:
        log.info('%s-job: skipping auto-notify — agent already notified user', job.user_slug)
        return 'skipped', None

    should_notify = False

    if job.notify == NotifyPolicy.ALWAYS:
        should_notify = True
    elif job.notify == NotifyPolicy.ON_FAILURE and run.status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
        # Apply alert cooldown: suppress until enough consecutive failures,
        # then respect cooldown between alerts.
        if job.consecutive_errors < job.alert_after_consecutive_failures:
            should_notify = False
        elif job.last_failure_alert_at:
            elapsed = (datetime.now(UTC) - job.last_failure_alert_at).total_seconds()
            should_notify = elapsed >= job.alert_cooldown_seconds
        else:
            should_notify = True
    elif job.notify == NotifyPolicy.ON_OUTPUT and run.output.strip():
        should_notify = True
    # SILENT: never notify

    if not should_notify:
        return 'skipped', None

    # Build notification message
    if run.status == RunStatus.COMPLETED:
        message = run.output.strip() if run.output.strip() else f'Job "{job.name}" completed.'
    elif run.status == RunStatus.TIMED_OUT:
        message = f'Job "{job.name}" timed out after {job.timeout_seconds}s'
    else:
        error_detail = run.error or 'unknown error'
        if job.consecutive_errors > 1:
            error_detail += f' ({job.consecutive_errors} consecutive failures)'
        message = f'Job "{job.name}" failed: {error_detail}'

    try:
        if job.channel == 'telegram':
            await _notify_telegram(job.user_slug, message)
        else:
            log.info('%s-job: notification channel=%s msg=%s', job.user_slug, job.channel, message[:100])

        # Track that we sent a failure alert (for cooldown)
        if run.status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
            job.last_failure_alert_at = datetime.now(UTC)
            save_job(job)

        return 'sent', None
    except Exception as exc:
        return 'failed', str(exc)


async def _notify_telegram(user_slug: str, message: str) -> None:
    """Send a notification via Telegram."""
    try:
        from marcel_core.channels.telegram import bot, sessions
        from marcel_core.channels.telegram.formatting import markdown_to_telegram_html

        chat_id = sessions.get_chat_id(user_slug)
        if chat_id:
            await bot.send_message(int(chat_id), markdown_to_telegram_html(message))
        else:
            log.warning('%s-job: no Telegram chat ID found', user_slug)
    except Exception:
        log.exception('%s-job: Telegram notification failed', user_slug)
