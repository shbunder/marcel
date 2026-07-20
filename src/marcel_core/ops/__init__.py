"""Operator surface for install + onboarding (FEAT-260707-6130cd).

The code-shaped, testable half of "simple installation": the guided
``setup.sh`` bootstrap orchestrates the host (secrets, zoo clone, systemd,
Docker) in shell, but the verifiable units live here as plain functions with
a thin CLI (``python -m marcel_core.ops``), so ``make`` targets and the
bootstrap script share one implementation:

- :mod:`.users` — ``add-user`` (the single onboarding path) and
  ``remove-user`` (archive, never delete — Core principle: Recoverable).
- :mod:`.telegram` — ``telegram-setup``: register + verify the webhook via
  the Bot API (no hand-written curl).
- :mod:`.doctor` — a post-install health check (server, webhook, zoo, users)
  with scriptable exit codes.
"""

from marcel_core.ops.doctor import run_doctor
from marcel_core.ops.telegram import setup_webhook
from marcel_core.ops.users import add_user, remove_user

__all__ = ['add_user', 'remove_user', 'run_doctor', 'setup_webhook']
