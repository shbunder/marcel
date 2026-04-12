"""The ``web`` god-tool — search, navigate, and interact with the web.

A single pydantic-ai tool that dispatches to many actions. Mirrors the
pattern used by :mod:`marcel_core.tools.marcel.dispatcher` and
:mod:`marcel_core.tools.integration`.

See :func:`marcel_core.tools.web.dispatcher.web` for the full action list
and docstring.
"""

from marcel_core.tools.web.dispatcher import web

__all__ = ['web']
