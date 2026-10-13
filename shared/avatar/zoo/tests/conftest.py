import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parents[3] / 'contracts' / 'avatar-sheet.schema.json'


@pytest.fixture(scope='session')
def js() -> dict:
    return json.loads((HERE / 'fixtures' / 'zoo-js.json').read_text())


@pytest.fixture(scope='session')
def schema() -> dict:
    return json.loads(SCHEMA.read_text())
