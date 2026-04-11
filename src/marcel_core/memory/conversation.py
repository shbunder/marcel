"""Segment-based continuous conversation storage.

Replaces session-based JSONL files with a single continuous conversation
per channel, split into manageable segments::

    data/users/{slug}/conversation/{channel}/
        segments/
            seg-0001.jsonl      # Sealed (summarized) segment
            seg-0002.jsonl      # Active segment (append-only)
        summaries/
            seg-0001.summary.md # Summary of seg-0001
        channel.meta.json       # Channel-level metadata
        search_index.jsonl      # Keyword search index

Segments are sealed when idle summarization or /forget runs. Tool results
are stripped from sealed segments to keep storage lean. A rolling summary
chain gives Marcel ambient awareness; the search index enables keyword recall.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from marcel_core.memory.history import HistoryMessage
from marcel_core.storage._atomic import atomic_write
from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)

# Segment size limits — rotate when either is exceeded.
MAX_SEGMENT_MESSAGES = 500
MAX_SEGMENT_BYTES = 500 * 1024  # 500KB

# Summary cap for inclusion in context window.
MAX_SUMMARY_TOKENS = 2000  # ~8000 chars at 4 chars/token
MAX_SUMMARY_CHARS = MAX_SUMMARY_TOKENS * 4

# Stopwords for keyword extraction (common English words to skip).
_STOPWORDS = frozenset(
    'a an the is are was were be been being have has had do does did will would '
    'shall should may might can could am to for of in on at by with from and or '
    'but not no nor so yet both either neither each every all any few more most '
    'other some such than too very just about above after again also as because '
    'before between into it its he she they them their this that these those my '
    'your his her our its me him us what which who whom how when where why i you '
    'we if then else up down out off over under ok okay yes right sure thanks '
    'thank please hi hello hey well like'.split()
)


# ---------------------------------------------------------------------------
# Channel metadata
# ---------------------------------------------------------------------------


@dataclass
class ChannelMeta:
    """Metadata for a continuous conversation channel."""

    channel: str
    created_at: datetime
    last_active: datetime
    active_segment: str
    next_segment_num: int
    total_messages: int = 0
    last_summary_at: datetime | None = None

    def to_dict(self) -> dict:
        d = {
            'channel': self.channel,
            'created_at': self.created_at.isoformat(),
            'last_active': self.last_active.isoformat(),
            'active_segment': self.active_segment,
            'next_segment_num': self.next_segment_num,
            'total_messages': self.total_messages,
        }
        if self.last_summary_at:
            d['last_summary_at'] = self.last_summary_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, obj: dict) -> ChannelMeta:
        return cls(
            channel=obj['channel'],
            created_at=datetime.fromisoformat(obj['created_at']),
            last_active=datetime.fromisoformat(obj['last_active']),
            active_segment=obj['active_segment'],
            next_segment_num=obj['next_segment_num'],
            total_messages=obj.get('total_messages', 0),
            last_summary_at=(datetime.fromisoformat(obj['last_summary_at']) if obj.get('last_summary_at') else None),
        )


# ---------------------------------------------------------------------------
# Summary metadata
# ---------------------------------------------------------------------------


@dataclass
class SegmentSummary:
    """Summary of a sealed conversation segment."""

    segment_id: str
    created_at: datetime
    trigger: str  # 'idle' | 'manual' | 'migration'
    message_count: int
    time_span_from: datetime
    time_span_to: datetime
    summary: str
    key_facts: list[str] = field(default_factory=list)
    previous_summary_segment: str | None = None

    def to_markdown(self) -> str:
        """Serialize to a markdown file with YAML-ish frontmatter."""
        lines = [
            '---',
            f'segment_id: {self.segment_id}',
            f'created_at: {self.created_at.isoformat()}',
            f'trigger: {self.trigger}',
            f'message_count: {self.message_count}',
            f'time_span_from: {self.time_span_from.isoformat()}',
            f'time_span_to: {self.time_span_to.isoformat()}',
        ]
        if self.previous_summary_segment:
            lines.append(f'previous_summary_segment: {self.previous_summary_segment}')
        lines.append('---')
        lines.append('')
        lines.append(self.summary)
        if self.key_facts:
            lines.append('')
            lines.append('## Key Facts')
            for fact in self.key_facts:
                lines.append(f'- {fact}')
        return '\n'.join(lines) + '\n'

    @classmethod
    def from_markdown(cls, text: str) -> SegmentSummary:
        """Parse from markdown file with frontmatter."""
        # Split frontmatter from body
        parts = text.split('---', 2)
        if len(parts) < 3:
            raise ValueError('Missing frontmatter delimiters')

        # Parse frontmatter as simple key: value pairs
        fm: dict[str, str] = {}
        for line in parts[1].strip().splitlines():
            if ':' in line:
                key, _, value = line.partition(':')
                fm[key.strip()] = value.strip()

        body = parts[2].strip()

        # Extract key facts from ## Key Facts section
        key_facts: list[str] = []
        summary_lines: list[str] = []
        in_facts = False
        for line in body.splitlines():
            if line.strip() == '## Key Facts':
                in_facts = True
                continue
            if in_facts and line.startswith('- '):
                key_facts.append(line[2:].strip())
            elif not in_facts:
                summary_lines.append(line)

        return cls(
            segment_id=fm['segment_id'],
            created_at=datetime.fromisoformat(fm['created_at']),
            trigger=fm['trigger'],
            message_count=int(fm['message_count']),
            time_span_from=datetime.fromisoformat(fm['time_span_from']),
            time_span_to=datetime.fromisoformat(fm['time_span_to']),
            summary='\n'.join(summary_lines).strip(),
            key_facts=key_facts,
            previous_summary_segment=fm.get('previous_summary_segment'),
        )


# ---------------------------------------------------------------------------
# Search index entry
# ---------------------------------------------------------------------------


@dataclass
class SearchEntry:
    """One entry in the keyword search index."""

    segment: str
    line: int
    timestamp: str
    keywords: list[str]
    role: str
    preview: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                'seg': self.segment,
                'line': self.line,
                'ts': self.timestamp,
                'kw': self.keywords,
                'role': self.role,
                'preview': self.preview,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_jsonl(cls, line: str) -> SearchEntry:
        obj = json.loads(line)
        return cls(
            segment=obj['seg'],
            line=obj['line'],
            timestamp=obj['ts'],
            keywords=obj['kw'],
            role=obj['role'],
            preview=obj['preview'],
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _conversation_dir(user_slug: str, channel: str) -> Path:
    return data_root() / 'users' / user_slug / 'conversation' / channel


def _segments_dir(user_slug: str, channel: str) -> Path:
    return _conversation_dir(user_slug, channel) / 'segments'


def _summaries_dir(user_slug: str, channel: str) -> Path:
    return _conversation_dir(user_slug, channel) / 'summaries'


def _meta_path(user_slug: str, channel: str) -> Path:
    return _conversation_dir(user_slug, channel) / 'channel.meta.json'


def _segment_path(user_slug: str, channel: str, segment_id: str) -> Path:
    return _segments_dir(user_slug, channel) / f'{segment_id}.jsonl'


def _summary_path(user_slug: str, channel: str, segment_id: str) -> Path:
    return _summaries_dir(user_slug, channel) / f'{segment_id}.summary.md'


def _search_index_path(user_slug: str, channel: str) -> Path:
    return _conversation_dir(user_slug, channel) / 'search_index.jsonl'


def _make_segment_id(num: int) -> str:
    return f'seg-{num:04d}'


# ---------------------------------------------------------------------------
# Channel metadata operations
# ---------------------------------------------------------------------------


def list_channels(user_slug: str) -> list[ChannelMeta]:
    """List all conversation channels for a user, sorted by last_active (newest first)."""
    conv_root = data_root() / 'users' / user_slug / 'conversation'
    if not conv_root.exists():
        return []
    channels: list[ChannelMeta] = []
    for channel_dir in conv_root.iterdir():
        if not channel_dir.is_dir():
            continue
        meta = load_channel_meta(user_slug, channel_dir.name)
        if meta is not None:
            channels.append(meta)
    channels.sort(key=lambda m: m.last_active, reverse=True)
    return channels


def load_channel_meta(user_slug: str, channel: str) -> ChannelMeta | None:
    """Load channel metadata, or None if no conversation exists."""
    path = _meta_path(user_slug, channel)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return ChannelMeta.from_dict(obj)
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        log.warning('Failed to load channel meta for %s/%s: %s', user_slug, channel, exc)
        return None


def save_channel_meta(user_slug: str, channel: str, meta: ChannelMeta) -> None:
    """Write channel metadata atomically."""
    path = _meta_path(user_slug, channel)
    atomic_write(path, json.dumps(meta.to_dict(), indent=2))


def ensure_channel(user_slug: str, channel: str) -> ChannelMeta:
    """Ensure a conversation channel exists, creating it if needed.

    Returns the channel metadata (existing or freshly created).
    """
    meta = load_channel_meta(user_slug, channel)
    if meta is not None:
        return meta

    now = datetime.now(tz=timezone.utc)
    segment_id = _make_segment_id(1)
    meta = ChannelMeta(
        channel=channel,
        created_at=now,
        last_active=now,
        active_segment=segment_id,
        next_segment_num=2,
    )

    # Create directory structure
    _segments_dir(user_slug, channel).mkdir(parents=True, exist_ok=True)
    _summaries_dir(user_slug, channel).mkdir(parents=True, exist_ok=True)

    # Create empty active segment
    seg_path = _segment_path(user_slug, channel, segment_id)
    seg_path.touch()

    save_channel_meta(user_slug, channel, meta)
    log.info('Created conversation channel %s/%s', user_slug, channel)
    return meta


# ---------------------------------------------------------------------------
# Segment read/write
# ---------------------------------------------------------------------------


def read_segment(user_slug: str, channel: str, segment_id: str) -> list[HistoryMessage]:
    """Read all messages from a segment JSONL file."""
    path = _segment_path(user_slug, channel, segment_id)
    if not path.exists():
        return []
    messages: list[HistoryMessage] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(HistoryMessage.from_jsonl(line))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.warning('Failed to parse segment line in %s: %s', segment_id, exc)
    return messages


def read_active_segment(user_slug: str, channel: str) -> list[HistoryMessage]:
    """Read all messages from the active (current) segment."""
    meta = load_channel_meta(user_slug, channel)
    if meta is None:
        return []
    return read_segment(user_slug, channel, meta.active_segment)


def append_to_segment(
    user_slug: str,
    channel: str,
    message: HistoryMessage,
) -> ChannelMeta:
    """Append a message to the active segment, rotating if needed.

    Returns the (possibly updated) channel metadata.
    """
    meta = ensure_channel(user_slug, channel)
    seg_path = _segment_path(user_slug, channel, meta.active_segment)

    # Check if we need to rotate (size-based, not summarization)
    needs_rotate = False
    if seg_path.exists():
        stat = seg_path.stat()
        if stat.st_size >= MAX_SEGMENT_BYTES:
            needs_rotate = True
        else:
            # Count messages (approximate: count lines)
            line_count = sum(1 for _ in open(seg_path, 'r', encoding='utf-8') if _.strip())
            if line_count >= MAX_SEGMENT_MESSAGES:
                needs_rotate = True

    if needs_rotate:
        meta = _rotate_segment(user_slug, channel, meta)
        seg_path = _segment_path(user_slug, channel, meta.active_segment)

    # Append message
    line = message.to_jsonl() + '\n'
    with open(seg_path, 'a', encoding='utf-8') as f:
        f.write(line)

    # Update metadata
    meta.last_active = datetime.now(tz=timezone.utc)
    meta.total_messages += 1
    save_channel_meta(user_slug, channel, meta)

    # Update search index for user/assistant text messages
    if message.role in ('user', 'assistant') and message.text:
        _append_search_index(user_slug, channel, meta.active_segment, message)

    return meta


def _rotate_segment(user_slug: str, channel: str, meta: ChannelMeta) -> ChannelMeta:
    """Create a new active segment (file rotation, not summarization)."""
    new_id = _make_segment_id(meta.next_segment_num)
    seg_path = _segment_path(user_slug, channel, new_id)
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.touch()

    meta.active_segment = new_id
    meta.next_segment_num += 1
    save_channel_meta(user_slug, channel, meta)
    log.info('Rotated to new segment %s for %s/%s', new_id, user_slug, channel)
    return meta


# ---------------------------------------------------------------------------
# Segment sealing (for summarization)
# ---------------------------------------------------------------------------


def seal_active_segment(user_slug: str, channel: str) -> tuple[str, ChannelMeta]:
    """Seal the current active segment and open a new one.

    Returns (sealed_segment_id, updated_meta).
    """
    meta = ensure_channel(user_slug, channel)
    sealed_id = meta.active_segment

    # Open new active segment
    new_id = _make_segment_id(meta.next_segment_num)
    seg_path = _segment_path(user_slug, channel, new_id)
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.touch()

    meta.active_segment = new_id
    meta.next_segment_num += 1
    meta.last_summary_at = datetime.now(tz=timezone.utc)
    save_channel_meta(user_slug, channel, meta)

    log.info('Sealed segment %s, new active: %s', sealed_id, new_id)
    return sealed_id, meta


def strip_tool_results_from_segment(user_slug: str, channel: str, segment_id: str) -> int:
    """Rewrite a sealed segment with tool results stripped.

    Tool-role messages are replaced with compact name-only notes.
    Returns the number of tool results stripped.
    """
    seg_path = _segment_path(user_slug, channel, segment_id)
    if not seg_path.exists():
        return 0

    messages = read_segment(user_slug, channel, segment_id)
    stripped = 0
    rewritten: list[str] = []

    for msg in messages:
        if msg.role == 'tool':
            # Replace with compact note
            compact = HistoryMessage(
                role='tool',
                text=f'[{msg.tool_name or "tool"}: completed]',
                timestamp=msg.timestamp,
                conversation_id=msg.conversation_id,
                tool_call_id=msg.tool_call_id,
                tool_name=msg.tool_name,
                result_ref=None,
                is_error=msg.is_error,
            )
            rewritten.append(compact.to_jsonl() + '\n')
            stripped += 1
        else:
            rewritten.append(msg.to_jsonl() + '\n')

    if stripped > 0:
        atomic_write(seg_path, ''.join(rewritten))
        log.debug('Stripped %d tool results from %s', stripped, segment_id)

    return stripped


# ---------------------------------------------------------------------------
# Summary operations
# ---------------------------------------------------------------------------


def save_summary(user_slug: str, channel: str, summary: SegmentSummary) -> None:
    """Save a segment summary to disk."""
    path = _summary_path(user_slug, channel, summary.segment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, summary.to_markdown())


def load_summary(user_slug: str, channel: str, segment_id: str) -> SegmentSummary | None:
    """Load a segment summary, or None if it doesn't exist."""
    path = _summary_path(user_slug, channel, segment_id)
    if not path.exists():
        return None
    try:
        return SegmentSummary.from_markdown(path.read_text(encoding='utf-8'))
    except (ValueError, KeyError) as exc:
        log.warning('Failed to parse summary %s: %s', segment_id, exc)
        return None


def load_latest_summary(user_slug: str, channel: str) -> SegmentSummary | None:
    """Load the most recent segment summary for a channel.

    Scans the summaries directory and returns the highest-numbered one.
    """
    summaries = _summaries_dir(user_slug, channel)
    if not summaries.exists():
        return None

    summary_files = sorted(summaries.glob('seg-*.summary.md'), reverse=True)
    for sf in summary_files:
        segment_id = sf.stem.removesuffix('.summary')
        summary = load_summary(user_slug, channel, segment_id)
        if summary is not None:
            return summary
    return None


def list_summaries(user_slug: str, channel: str) -> list[str]:
    """List all segment IDs that have summaries, oldest first."""
    summaries = _summaries_dir(user_slug, channel)
    if not summaries.exists():
        return []
    return sorted(sf.stem.removesuffix('.summary') for sf in summaries.glob('seg-*.summary.md'))


# ---------------------------------------------------------------------------
# Keyword search
# ---------------------------------------------------------------------------


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from text for search indexing.

    Simple tokenization: split on non-alphanumeric, lowercase, dedupe,
    filter stopwords and short tokens.
    """
    tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        if len(token) < 3:
            continue
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords


def _append_search_index(
    user_slug: str,
    channel: str,
    segment_id: str,
    message: HistoryMessage,
) -> None:
    """Append a search index entry for a user/assistant message."""
    if not message.text:
        return

    keywords = extract_keywords(message.text)
    if not keywords:
        return

    # Count current line in segment for the line reference
    seg_path = _segment_path(user_slug, channel, segment_id)
    line_num = sum(1 for _ in open(seg_path, 'r', encoding='utf-8') if _.strip())

    preview = message.text[:150].replace('\n', ' ')
    entry = SearchEntry(
        segment=segment_id,
        line=line_num,
        timestamp=message.timestamp.isoformat(),
        keywords=keywords[:20],  # Cap keywords per entry
        role=message.role,
        preview=preview,
    )

    index_path = _search_index_path(user_slug, channel)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, 'a', encoding='utf-8') as f:
        f.write(entry.to_jsonl() + '\n')


def search_conversations(
    user_slug: str,
    channel: str,
    query: str,
    max_results: int = 5,
) -> list[tuple[SearchEntry, list[HistoryMessage]]]:
    """Search conversation history by keyword.

    Returns matching entries with surrounding context (up to 5 messages
    before and after each match).
    """
    index_path = _search_index_path(user_slug, channel)
    if not index_path.exists():
        return []

    query_keywords = set(extract_keywords(query))
    if not query_keywords:
        return []

    # Score entries by keyword overlap
    scored: list[tuple[int, SearchEntry]] = []
    with open(index_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = SearchEntry.from_jsonl(line)
            except (json.JSONDecodeError, KeyError):
                continue
            overlap = len(query_keywords & set(entry.keywords))
            if overlap > 0:
                scored.append((overlap, entry))

    # Sort by overlap score (descending), take top results
    scored.sort(key=lambda x: x[0], reverse=True)
    top_entries = [entry for _, entry in scored[:max_results]]

    # Load surrounding context for each match
    results: list[tuple[SearchEntry, list[HistoryMessage]]] = []
    # Cache segments to avoid re-reading
    segment_cache: dict[str, list[HistoryMessage]] = {}

    for entry in top_entries:
        if entry.segment not in segment_cache:
            segment_cache[entry.segment] = read_segment(user_slug, channel, entry.segment)

        messages = segment_cache[entry.segment]
        # Get surrounding context (5 before, 5 after)
        start = max(0, entry.line - 6)  # line is 1-indexed after append
        end = min(len(messages), entry.line + 5)
        context = messages[start:end]
        results.append((entry, context))

    return results


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


def is_idle(user_slug: str, channel: str, idle_minutes: int = 60) -> bool:
    """Check if a channel has been idle for longer than the threshold.

    Returns True if last_active is older than idle_minutes ago.
    """
    meta = load_channel_meta(user_slug, channel)
    if meta is None:
        return False
    elapsed = datetime.now(tz=timezone.utc) - meta.last_active
    return elapsed.total_seconds() > idle_minutes * 60


def has_active_content(user_slug: str, channel: str) -> bool:
    """Check if the active segment has any messages worth summarizing."""
    meta = load_channel_meta(user_slug, channel)
    if meta is None:
        return False
    seg_path = _segment_path(user_slug, channel, meta.active_segment)
    if not seg_path.exists():
        return False
    # Check for at least one user message
    with open(seg_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get('role') == 'user':
                    return True
            except json.JSONDecodeError:
                continue
    return False
