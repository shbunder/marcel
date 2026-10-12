"""The environment every `claude` the runner starts gets."""

from collections.abc import Mapping
from pathlib import Path

SCRUBBED_PREFIXES = ('CLAUDE', 'ANTHROPIC_')


def scrubbed_env(env: Mapping[str, str], home: Path | None = None) -> dict[str, str]:
    """`env` without any `CLAUDE*` or `ANTHROPIC_*` variable.

    A parent Claude session's markers turn transcript saving off (SP2), and an inherited
    `ANTHROPIC_API_KEY` or `CLAUDE_CONFIG_DIR` would make claude use another account than the
    owner's subscription. With `home`, `HOME` is set to it.
    """
    out = {k: v for k, v in env.items() if not k.startswith(SCRUBBED_PREFIXES)}
    if home is not None:
        out['HOME'] = str(home)
    return out
