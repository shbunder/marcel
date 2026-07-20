"""Post-turn memory formation for Marcel (FEAT-260720-697528).

Two fire-and-forget agents that run *after* a turn: :mod:`.extract` distils
durable facts into the per-user memory store, and :mod:`.summarizer` seals and
summarizes idle conversation segments. The conversation/history/paste
*substrate* they operate over lives in :mod:`marcel_core.storage`
(``storage.conversation``, ``storage.history``, ``storage.pastes``); the
distilled-fact store is :mod:`marcel_core.storage.memory`; the harness Memory
capability wiring is :mod:`marcel_core.capabilities.memory`.
"""

from .extract import extract_and_save_memories

__all__ = [
    'extract_and_save_memories',
]
