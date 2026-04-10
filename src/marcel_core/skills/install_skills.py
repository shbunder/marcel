"""Seed default skills into the data root.

Skills live at ``<data_root>/skills/`` (``~/.marcel/skills/``).  Default
skills are bundled in ``src/marcel_core/defaults/skills/`` and seeded
automatically on server startup if not already present.

This script can be run manually to force-seed defaults.
"""

if __name__ == '__main__':
    from marcel_core.defaults import seed_defaults
    from marcel_core.storage._root import data_root

    seed_defaults(data_root())
    print(f'Defaults seeded to {data_root()}')
