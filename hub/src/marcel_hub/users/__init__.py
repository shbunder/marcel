"""The one person an install belongs to (ADR-013) and their first buddy.

`ensure_owner` makes the owner's row at start-up; `marcel-hub setup` (`users.cli`) makes the owner
and their first buddy on a fresh install.
"""

from marcel_hub.users.store import ensure_owner, ensure_owner_agent, give_missing_looks

__all__ = ['ensure_owner', 'ensure_owner_agent', 'give_missing_looks']
