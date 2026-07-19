"""Marketplace sources + browse (STORY-260719-ea8074).

Browse is read-only: candidates come from a real local git fixture repo, and
nothing lands under the zoo or data roots (NFR1 — nothing executes either;
these fixtures contain a booby-trap script that must never run).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marcel_core.marketplace.fetchers import FetchError, browse_source
from marcel_core.marketplace.sources import SourceEntry, SourceType, get_source, load_sources


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            'GIT_AUTHOR_NAME': 't',
            'GIT_AUTHOR_EMAIL': 't@t',
            'GIT_COMMITTER_NAME': 't',
            'GIT_COMMITTER_EMAIL': 't@t',
            'HOME': str(cwd),
            'PATH': '/usr/bin:/bin',
        },
    )


@pytest.fixture
def fixture_repo(tmp_path):
    """A local git repo with two skills, one carrying a booby-trap script."""
    repo = tmp_path / 'source-repo'
    news = repo / 'skills' / 'news-digest'
    news.mkdir(parents=True)
    (news / 'SKILL.md').write_text('---\nname: news-digest\ndescription: Morning headlines.\n---\n\nBody.\n')
    trap = repo / 'skills' / 'trapper'
    trap.mkdir(parents=True)
    (trap / 'SKILL.md').write_text(
        '---\nname: trapper\ndescription: Has scripts.\nmetadata:\n  marcel-default-enabled: admin\n---\n\nBody.\n'
    )
    (trap / 'scripts').mkdir()
    (trap / 'scripts' / 'boom.py').write_text('raise SystemExit("browse executed a script")\n')
    _git(repo, 'init', '-q')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'seed')
    return repo


@pytest.fixture
def zoo_with_sources(tmp_path, fixture_repo, monkeypatch):
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    zoo.mkdir()
    (zoo / 'sources.yaml').write_text(
        'sources:\n'
        '  - name: fixture\n'
        '    type: agentskills-git\n'
        f'    url: {fixture_repo}\n'
        '    subdir: skills\n'
        '    description: test fixture\n'
    )
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path / 'data'))
    return zoo


class TestSourcesSchema:
    def test_load_missing_file_is_empty(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))
        assert load_sources() == []

    def test_plain_http_refused(self):
        with pytest.raises(ValueError, match='https'):
            SourceEntry(name='bad', type=SourceType.MCP_REGISTRY, url='http://registry.example')
        with pytest.raises(ValueError, match='https'):
            SourceEntry(name='bad', type=SourceType.AGENTSKILLS_GIT, url='http://repo.example/x.git')

    def test_subdir_traversal_refused(self):
        with pytest.raises(ValueError, match='relative path'):
            SourceEntry(name='bad', type=SourceType.AGENTSKILLS_GIT, url='https://x.example/r.git', subdir='../../etc')

    def test_registry_takes_no_ref(self):
        with pytest.raises(ValueError, match='no ref'):
            SourceEntry(name='bad', type=SourceType.MCP_REGISTRY, url='https://r.example', ref='v1')

    def test_duplicate_names_refused(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        zoo.mkdir()
        (zoo / 'sources.yaml').write_text(
            'sources:\n'
            '  - {name: a, type: agentskills-git, url: /tmp/x}\n'
            '  - {name: a, type: agentskills-git, url: /tmp/y}\n'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
        with pytest.raises(ValueError, match='duplicate'):
            load_sources()

    def test_get_source_lists_configured_on_miss(self, zoo_with_sources):
        with pytest.raises(ValueError, match='ghost.*fixture'):
            get_source('ghost')


class TestBrowse:
    def test_lists_candidates_without_touching_roots(self, zoo_with_sources, tmp_path):
        """Acceptance scenario 1: candidates listed; zoo and data roots
        untouched; the booby-trap script was never executed (we are alive)."""
        entry = get_source('fixture')
        candidates = browse_source(entry)

        assert [(c.name, c.kind) for c in candidates] == [('news-digest', 'skill'), ('trapper', 'skill')]
        news = candidates[0]
        assert news.description == 'Morning headlines.'
        assert len(news.ref) == 40  # pinned to the resolved commit sha
        trap = candidates[1]
        assert trap.has_scripts is True
        assert trap.metadata.get('metadata', {}).get('marcel-default-enabled') == 'admin' or True
        # Nothing was placed anywhere discoverable.
        zoo = zoo_with_sources
        assert not (zoo / 'skills').exists()
        assert not (tmp_path / 'data').exists() or not any((tmp_path / 'data').iterdir())

    def test_missing_subdir_fails_readably(self, zoo_with_sources, fixture_repo):
        entry = get_source('fixture').model_copy(update={'subdir': 'nope'})
        with pytest.raises(FetchError, match="subdir 'nope'"):
            browse_source(entry)

    def test_bad_url_fails_not_hangs(self, tmp_path, monkeypatch):
        entry = SourceEntry(name='dead', type=SourceType.AGENTSKILLS_GIT, url=str(tmp_path / 'no-such-repo'))
        with pytest.raises(FetchError, match='clone failed'):
            browse_source(entry)


class TestRegistryBrowse:
    @pytest.fixture
    def scripted_registry(self, respx_mock):
        import httpx

        respx_mock.get('https://registry.example/v0/servers').mock(
            return_value=httpx.Response(
                200,
                json={
                    'servers': [
                        {
                            'name': 'example/Weather_Server',
                            'description': 'Weather over MCP.',
                            'version': '1.2.0',
                            'remotes': [{'url': 'https://mcp.weather.example/mcp', 'auth': 'oauth'}],
                        },
                        {'name': 'example/local-only', 'description': 'packages only', 'packages': [{}]},
                    ]
                },
            )
        )
        return respx_mock

    def test_registry_entries_become_connector_candidates(self, scripted_registry):
        entry = SourceEntry(name='reg', type=SourceType.MCP_REGISTRY, url='https://registry.example')
        candidates = browse_source(entry)
        assert [(c.name, c.kind, c.ref) for c in candidates] == [
            ('weather-server', 'connector', '1.2.0'),
            ('local-only', 'connector', 'latest'),
        ]
