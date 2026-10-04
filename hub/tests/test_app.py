import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy import create_engine, inspect

from marcel_hub import __version__
from marcel_hub.app import Settings, SettingsError, create_app, load_settings

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'


def health_schema() -> Draft202012Validator:
    spec = yaml.safe_load(CONTRACT.read_text())
    resource = Resource.from_contents(
        spec | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}
    )
    registry = Registry().with_resource('urn:contract', resource)
    return Draft202012Validator(
        {'$ref': 'urn:contract#/components/schemas/Health'}, registry=registry
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')


def test_health_matches_the_contract(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        res = client.get('/api/health')
    assert res.status_code == 200
    body = res.json()
    health_schema().validate(body)
    assert body['ok'] is True
    assert body['version'] == __version__


def test_health_says_why_the_runner_is_unreachable(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        runner = client.get('/api/health').json()['runner']
    assert runner['reachable'] is False
    assert runner['reason']


def test_health_is_not_ok_when_the_database_is_down(settings: Settings, tmp_path: Path) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.engine = create_engine(f'sqlite:///{tmp_path}/missing-dir/none.db')
        body = client.get('/api/health').json()
    health_schema().validate(body)
    assert body['ok'] is False


def test_starting_the_app_migrates_the_database(settings: Settings) -> None:
    app = create_app(settings)
    assert not settings.db_path.exists()
    with TestClient(app):
        assert 'task' in inspect(app.state.engine).get_table_names()


def test_health_needs_no_device_token(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/health', headers={}).status_code == 200


def test_health_is_only_under_api(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').status_code == 404


def test_settings_default_without_a_file(tmp_path: Path) -> None:
    s = load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml')})
    assert (s.data_dir, s.db_path, s.port) == (Path('/data'), Path('/data/marcel.db'), 7420)


def test_settings_read_the_file(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text(f'data_dir = "{tmp_path}"\nport = 9000\n')
    s = load_settings({'MARCEL_CONFIG': str(cfg)})
    assert (s.port, s.db_path) == (9000, tmp_path / 'marcel.db')


def test_environment_wins_over_the_file(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('port = 9000\n')
    s = load_settings({'MARCEL_CONFIG': str(cfg), 'MARCEL_PORT': '9100'})
    assert s.port == 9100


def test_settings_reject_a_broken_file(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('port = = 1')
    with pytest.raises(SettingsError, match=r'marcel\.toml'):
        load_settings({'MARCEL_CONFIG': str(cfg)})


def test_settings_reject_unknown_keys(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('prot = 1\n')
    with pytest.raises(SettingsError, match='prot'):
        load_settings({'MARCEL_CONFIG': str(cfg)})


def test_settings_reject_a_bad_port(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match='port'):
        load_settings({'MARCEL_CONFIG': str(tmp_path / 'x'), 'MARCEL_PORT': 'abc'})


def test_create_app_reads_settings_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    app = create_app()
    assert app.state.settings.db_path == tmp_path / 'marcel.db'
    json.dumps(app.openapi())  # the schema builds
