"""Per-user credential storage for plugin habitats.

Credentials are stored encrypted under ``<data_root>/users/{slug}/credentials.enc``
when ``MARCEL_CREDENTIALS_KEY`` is set, with a plaintext fallback otherwise
(see :mod:`marcel_core.storage.credentials` for the full mechanism).

This module is part of the stable plugin surface — the function names here
won't change between Marcel versions without a migration note. Habitats
should import from here, not from ``marcel_core.storage.credentials``.

Example::

    from marcel_core.plugin import credentials

    creds = credentials.load(user_slug)
    api_key = creds.get('MY_SERVICE_API_KEY')

    creds['MY_SERVICE_API_KEY'] = new_value
    credentials.save(user_slug, creds)
"""

from __future__ import annotations

from marcel_core.storage.credentials import load_credentials as load, save_credentials as save

__all__ = ['load', 'save']
