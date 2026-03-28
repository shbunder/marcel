"""Marcel mascot renderer.

Loads the block-character mascot from ``design/mascot.txt`` and renders it
in Blush Rose (``#cc5e76``) using Rich.  Import :func:`print_mascot` for a
one-shot terminal print, or use :func:`mascot_text` when you need a
:class:`rich.text.Text` object to embed in a Textual widget.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from rich.console import Console
from rich.style import Style
from rich.text import Text

# ---------------------------------------------------------------------------
# Brand colour
# ---------------------------------------------------------------------------

BLUSH_ROSE = Style(color='#cc5e76')

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _art() -> str:
    """Return the raw mascot art string."""
    # Prefer the file relative to the repo root so it works in development and
    # installed environments alike.
    candidate = Path(__file__).parents[2] / 'design' / 'mascot.txt'
    if candidate.exists():
        return candidate.read_text(encoding='utf-8')

    # Fallback: embedded copy so the package works when installed without the
    # design/ directory being present.
    return (
        '     ▖▄\n'
        '▄▄▄▄▄▙▙\n'
        '▛███ ██\n'
        '▀▀▀▀▀██\n'
        '     ▙█\n'
        '     █▜\n'
        '     ▛█\n'
        '     █▜▄▄▄\n'
        '     ▛▛▀▛▛▘\n'
        '     ▌▌ ▌▌\n'
        '     ▌▌ ▌▌\n'
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mascot_text() -> Text:
    """Return the mascot as a :class:`rich.text.Text` styled in Blush Rose.

    Use this when you need to embed the mascot inside a Textual widget or
    compose it with other Rich renderables.

    Returns:
        A ``Text`` object with the full mascot art coloured ``#cc5e76``.
    """
    return Text(_art().rstrip('\n'), style=BLUSH_ROSE, no_wrap=True)


def print_mascot(console: Console | None = None) -> None:
    """Print the mascot to the terminal in Blush Rose.

    Args:
        console: Optional Rich :class:`~rich.console.Console` to use.
            Creates a default one if not provided.
    """
    if console is None:
        console = Console()
    console.print(mascot_text())
