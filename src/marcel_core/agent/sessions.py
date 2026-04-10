"""Session manager — persistent ClaudeSDKClient instances per conversation.

Each active conversation gets its own ClaudeSDKClient that maintains
conversation state across turns, enabling prompt cache reuse and SDK-managed
context compaction.  Idle sessions are cleaned up after a configurable timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from marcel_core.agent.context import build_system_prompt
from marcel_core.skills import build_skills_mcp_server
from marcel_core.tools.browser import browser_manager, build_browser_mcp_server, is_available as browser_available

log = logging.getLogger(__name__)

# Default idle timeout: 1 hour.
DEFAULT_IDLE_TIMEOUT = 3600

# How often the cleanup task runs (seconds).
_CLEANUP_INTERVAL = 300


@dataclass
class ActiveSession:
    """A live ClaudeSDKClient session for one user + conversation."""

    client: ClaudeSDKClient
    user_slug: str
    conversation_id: str
    channel: str
    last_active: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_active = time.monotonic()


class SessionManager:
    """Manages ClaudeSDKClient lifecycles keyed by (user_slug, conversation_id)."""

    def __init__(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None:
        self._sessions: dict[tuple[str, str], ActiveSession] = {}
        self._idle_timeout = idle_timeout
        self._cleanup_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create(
        self,
        user_slug: str,
        conversation_id: str,
        channel: str,
        model: str | None = None,
    ) -> ActiveSession:
        """Return an existing session or create and connect a new one."""
        key = (user_slug, conversation_id)
        session = self._sessions.get(key)
        if session is not None:
            session.touch()
            return session

        system_prompt = build_system_prompt(user_slug, channel)

        mcp_servers: dict = {'skills': build_skills_mcp_server(user_slug, channel)}
        if browser_available():
            session_key = f'{user_slug}:{conversation_id}'
            mcp_servers['browser'] = build_browser_mcp_server(session_key, browser_manager)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            tools={'type': 'preset', 'preset': 'claude_code'},
            mcp_servers=mcp_servers,
            permission_mode='bypassPermissions',
            max_turns=75,
            model=model,
        )

        client = ClaudeSDKClient(options)
        await client.connect()

        session = ActiveSession(
            client=client,
            user_slug=user_slug,
            conversation_id=conversation_id,
            channel=channel,
        )
        self._sessions[key] = session
        log.info(
            'Created session for user=%s conversation=%s (active=%d)',
            user_slug,
            conversation_id,
            len(self._sessions),
        )
        return session

    async def disconnect(self, user_slug: str, conversation_id: str) -> None:
        """Disconnect and remove a specific session."""
        key = (user_slug, conversation_id)
        session = self._sessions.pop(key, None)
        if session is not None:
            await self._disconnect_session(session)

    async def disconnect_all(self) -> None:
        """Disconnect all active sessions.  Call during shutdown."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            await self._disconnect_session(session)

    async def reset_user(self, user_slug: str) -> None:
        """Disconnect all sessions for a given user (e.g. on /new)."""
        keys = [k for k in self._sessions if k[0] == user_slug]
        for key in keys:
            session = self._sessions.pop(key)
            await self._disconnect_session(session)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Idle cleanup
    # ------------------------------------------------------------------

    def start_cleanup_loop(self) -> None:
        """Start the background idle-cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def stop_cleanup_loop(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    async def cleanup_idle(self) -> int:
        """Disconnect sessions idle longer than the timeout. Returns count removed."""
        now = time.monotonic()
        stale_keys = [key for key, session in self._sessions.items() if now - session.last_active > self._idle_timeout]
        for key in stale_keys:
            session = self._sessions.pop(key)
            log.info(
                'Cleaning up idle session user=%s conversation=%s',
                session.user_slug,
                session.conversation_id,
            )
            await self._disconnect_session(session)
        return len(stale_keys)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _disconnect_session(self, session: ActiveSession) -> None:
        # Clean up browser context for this session
        session_key = f'{session.user_slug}:{session.conversation_id}'
        await browser_manager.close_context(session_key)

        try:
            await session.client.disconnect()
        except Exception:
            log.exception(
                'Error disconnecting session user=%s conversation=%s',
                session.user_slug,
                session.conversation_id,
            )

    async def _cleanup_loop(self) -> None:
        """Periodically clean up idle sessions."""
        try:
            while True:
                await asyncio.sleep(_CLEANUP_INTERVAL)
                removed = await self.cleanup_idle()
                if removed:
                    log.info('Idle cleanup: removed %d session(s), %d active', removed, len(self._sessions))
        except asyncio.CancelledError:
            pass


# Module-level singleton — initialised once, shared across the app.
session_manager = SessionManager()
