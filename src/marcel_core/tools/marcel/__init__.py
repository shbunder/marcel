"""Unified internal utilities tool for Marcel.

This package implements the ``marcel`` tool — a single pydantic-ai tool that
dispatches to many internal actions (read_skill, search_memory, save_memory,
search_conversations, compact, notify, list_models, get_model, set_model).

The goal of the single-tool design is to keep the LLM tool list small: one
``marcel`` tool is advertised instead of ten separate ones. At the cost of
looking like a god-object from the outside, this dramatically reduces prompt
token usage and tool-selection confusion.

Internally, each action is implemented in its own sub-module so the code
stays modular and easy to navigate:

- :mod:`.skills` — ``read_skill``, ``read_skill_resource``
- :mod:`.memory` — ``search_memory``, ``read_memory``, ``save_memory``
- :mod:`.conversations` — ``search_conversations``, ``compact``
- :mod:`.notifications` — ``notify`` (and ``send_notify`` for in-process callers)
- :mod:`.settings` — ``list_models``, ``get_model``, ``set_model``

External capabilities (browser, bash, file I/O, charts) and integration
dispatch remain as separate tools. Only **internal** Marcel utilities live
here.
"""

from __future__ import annotations

from .dispatcher import marcel
from .notifications import send_notify

__all__ = ['marcel', 'send_notify']
