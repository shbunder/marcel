from __future__ import annotations

import pytest

from marcel_mock_hub.settings import DEFAULT_PORT, port


def test_the_default_port_is_7499():
    assert port({}) == DEFAULT_PORT == 7499


def test_mock_hub_port_picks_another_port():
    assert port({'MOCK_HUB_PORT': '7488'}) == 7488


@pytest.mark.parametrize('value', ['seventy', '0', '70000', '-1'])
def test_a_bad_port_stops_with_a_plain_message(value: str):
    with pytest.raises(SystemExit, match='MOCK_HUB_PORT must be a port number'):
        port({'MOCK_HUB_PORT': value})
