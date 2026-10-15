import json
import logging
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy import create_engine, inspect

from marcel_hub import __version__
from marcel_hub.app import Settings, SettingsError, create_app, load_settings, main

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
    s = load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml'), 'MARCEL_HOME': '/m'})
    assert (s.data_dir, s.db_path, s.port) == (Path('/m/data'), Path('/m/data/marcel.db'), 7420)


def test_every_default_path_is_under_marcel_home(tmp_path: Path) -> None:
    home = tmp_path / 'x'
    s = load_settings({'MARCEL_HOME': str(home)})
    paths = [s.data_dir, s.db_path, s.redeploy_flag, s.runner_sock]
    paths += [Path(p) for p in (s.brain_cwd, s.worker_cwd) if p is not None]
    assert paths == [
        home / 'data',
        home / 'data' / 'marcel.db',
        home / 'state' / 'redeploy.flag',
        home / 'run' / 'runner.sock',
        home / 'brain',
        home / 'workspaces' / 'scratch',
    ]
    assert s.host == '127.0.0.1', 'only the tunnel reaches the hub from outside'


def test_marcel_home_is_dot_marcel_in_the_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    s = load_settings({})  # no MARCEL_HOME: the owner's decision, 2026-10-15
    assert s.data_dir == tmp_path / '.marcel' / 'data'
    assert s.runner_sock == tmp_path / '.marcel' / 'run' / 'runner.sock'


def test_the_config_file_is_read_from_marcel_home(tmp_path: Path) -> None:
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'marcel.toml').write_text('owner_name = "Ann"\nport = 7500\n')
    s = load_settings({'MARCEL_HOME': str(tmp_path)})
    assert (s.owner_name, s.port) == ('Ann', 7500)


def test_an_explicit_setting_still_wins_over_marcel_home(tmp_path: Path) -> None:
    data = tmp_path / 'elsewhere'
    data.mkdir()
    (data / 'marcel.toml').write_text('owner_name = "Bo"\n')
    s = load_settings({'MARCEL_HOME': str(tmp_path / 'x'), 'MARCEL_DATA_DIR': str(data),
                       'MARCEL_HOST': '0.0.0.0', 'MARCEL_BRAIN_CWD': '/b'})  # fmt: skip
    assert (s.data_dir, s.db_path) == (data, data / 'marcel.db')
    assert s.owner_name == 'Bo', 'the config file follows MARCEL_DATA_DIR'
    assert (s.host, s.brain_cwd) == ('0.0.0.0', '/b')


def test_the_docker_hub_never_falls_back_to_marcel_home(tmp_path: Path) -> None:
    """deploy/docker-compose.yml sets every path; HOME is /tmp in the container."""
    import yaml

    compose = Path(__file__).resolve().parents[2] / 'deploy' / 'docker-compose.yml'
    env = dict(yaml.safe_load(compose.read_text())['services']['hub']['environment'])
    env = {k: str(v).replace('${MARCEL_HOME}', '/home/o/marcel') for k, v in env.items()}
    (tmp_path / 'marcel.toml').write_text('')
    env['MARCEL_CONFIG'] = str(tmp_path / 'marcel.toml')  # /data/marcel.toml in the container
    s = load_settings(env)
    assert s.host == '0.0.0.0'
    paths = [s.data_dir, s.db_path, s.redeploy_flag, s.runner_sock, s.brain_cwd, s.worker_cwd]
    assert not any(str(p).startswith('/tmp') for p in paths), paths
    assert env['HOME'] == '/tmp'


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


def test_a_failing_migration_stops_the_hub_at_start(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_engine: object) -> None:
        raise RuntimeError('migration 0002 failed')

    monkeypatch.setattr('marcel_hub.db.upgrade', boom)
    with pytest.raises(RuntimeError, match='0002'), TestClient(create_app(settings)):
        pass


def test_settings_reject_a_path_of_the_wrong_type(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('data_dir = 5\n')
    with pytest.raises(SettingsError, match=r'marcel\.toml'):
        load_settings({'MARCEL_CONFIG': str(cfg)})


def test_marcel_hub_serves_on_the_configured_host_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr('uvicorn.run', lambda app, **kw: calls.append({'app': app, **kw}))
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MARCEL_HOST', '127.0.0.1')
    monkeypatch.setenv('MARCEL_PORT', '9123')
    main()
    assert len(calls) == 1
    assert (calls[0]['host'], calls[0]['port']) == ('127.0.0.1', 9123)
    assert calls[0]['app'].state.settings.db_path == tmp_path / 'marcel.db'  # type: ignore[attr-defined]


def test_marcel_hub_refuses_to_start_on_broken_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('port = = 1')
    monkeypatch.setenv('MARCEL_CONFIG', str(cfg))
    monkeypatch.setattr('uvicorn.run', lambda *a, **kw: pytest.fail('should not serve'))
    with pytest.raises(SystemExit, match=r'did not start.*marcel\.toml'):
        main()


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (None, None),
        ('', None),
        ('https://mac.tail1234.ts.net', 'https://mac.tail1234.ts.net'),
        ('https://marcel-bot.com/', 'https://marcel-bot.com'),
    ],
)
def test_public_url_is_this_hubs_own_https_address(
    tmp_path: Path, value: str | None, expected: str | None
) -> None:
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml')}
    if value is not None:
        env['MARCEL_PUBLIC_URL'] = value
    assert load_settings(env).public_url == expected


@pytest.mark.parametrize(
    ('value', 'words'),
    [('http://mac.local', 'https address'), ('https://mac.example.org/api', 'without /api')],
)
def test_a_public_url_that_cannot_work_stops_the_hub(
    tmp_path: Path, value: str, words: str
) -> None:
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml'), 'MARCEL_PUBLIC_URL': value}
    with pytest.raises(SettingsError, match=words):
        load_settings(env)


def test_without_a_public_url_the_hub_starts_and_says_so_once(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), TestClient(create_app(settings)):
        pass
    assert caplog.text.count('No public address yet: run `marcel connect`.') == 1
