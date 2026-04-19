"""Seed bundled defaults into the data root.

The kernel ships zero bundled skills — every skill lives in marcel-zoo
and is loaded from ``MARCEL_ZOO_DIR`` at runtime. ``seed_defaults()``
still copies channel prompts, ``routing.yaml``, and subagent definitions
from ``src/marcel_core/defaults/`` to the data root on first startup.

This script can be run manually to force-seed the non-skill defaults.
"""

if __name__ == '__main__':
    from marcel_core.defaults import seed_defaults
    from marcel_core.storage._root import data_root

    seed_defaults(data_root())
    print(f'Defaults seeded to {data_root()}')
