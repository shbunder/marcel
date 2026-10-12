from pathlib import Path

import pytest

BRAIN = Path(__file__).resolve().parents[1]
ROOT = BRAIN.parent


@pytest.fixture
def values(tmp_path: Path) -> dict[str, str]:
    """A full set of deploy values, with paths under the test's own folder."""
    return {
        'OWNER_NAME': 'Shaun',
        'MARKETPLACE_DIR': str(tmp_path / 'plugins'),
        'MEMORY_DIR': str(tmp_path / 'data' / 'memory'),
    }
