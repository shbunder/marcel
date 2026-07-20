"""Install / update / remove flows (STORY-260719-74c7df, 2fe958, d856fc).

Every scenario runs against a real local git fixture source and a real git
zoo checkout — commits, provenance and manifest state are asserted on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from marcel_core.marketplace.installer import (
    InstallError,
    install,
    remove,
    review_install,
    review_update,
    update,
)

_WHO = {'user_slug': 'shaun', 'channel': 'cli'}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    monkeypatch.setenv('GIT_AUTHOR_NAME', 't')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 't@t')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 't')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 't@t')


@pytest.fixture
def fixture_repo(tmp_path):
    repo = tmp_path / 'source-repo'
    good = repo / 'skills' / 'news-digest'
    good.mkdir(parents=True)
    (good / 'SKILL.md').write_text(
        '---\nname: news-digest\ndescription: Morning headlines.\n'
        'metadata:\n  marcel-default-enabled: admin\n---\n\nBody.\n'
    )
    (good / 'feeds.yaml').write_text('feeds: []\n')
    bad = repo / 'skills' / 'mismatched'
    bad.mkdir(parents=True)
    (bad / 'SKILL.md').write_text('---\nname: other-name\ndescription: Name/dir mismatch.\n---\n\nBody.\n')
    _git(repo, 'init', '-q')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'seed')
    return repo


@pytest.fixture
def roots(tmp_path, fixture_repo, monkeypatch):
    """A git zoo with sources.yaml, a data root, and one admin user."""
    from marcel_core.config import settings
    from marcel_core.storage import _root

    zoo = tmp_path / 'zoo'
    zoo.mkdir()
    (zoo / 'sources.yaml').write_text(
        f'sources:\n  - name: fixture\n    type: agentskills-git\n    url: {fixture_repo}\n    subdir: skills\n'
    )
    _git(zoo, 'init', '-q')
    _git(zoo, 'add', 'sources.yaml')
    _git(zoo, 'commit', '-qm', 'zoo seed')

    data = tmp_path / 'data'
    profile = data / 'users' / 'shaun'
    profile.mkdir(parents=True)
    (profile / 'profile.md').write_text('---\nrole: admin\n---\n')
    kid = data / 'users' / 'kiddo'
    kid.mkdir(parents=True)
    (kid / 'profile.md').write_text('---\nrole: user\n---\n')

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data))
    monkeypatch.setattr(_root, '_DATA_ROOT', data)
    return zoo, data


class TestInstall:
    def test_install_lands_as_reviewed_commit_and_seeds(self, roots):
        """Acceptance scenario 2: review → token → validate → place → commit
        → enablement seeded from marcel-default-enabled."""
        zoo, data = roots
        review = review_install('fixture', 'news-digest', **_WHO)
        assert 'news-digest' in review.summary and 'Morning headlines' in review.summary

        result = install('fixture', 'news-digest', review.token, **_WHO)

        installed = zoo / 'skills' / 'news-digest'
        assert (installed / 'SKILL.md').is_file() and (installed / 'feeds.yaml').is_file()
        provenance = yaml.safe_load((installed / '.marcel-provenance.yaml').read_text())
        assert provenance['source'] == 'fixture' and len(provenance['ref']) == 40

        # The commit contains exactly that folder.
        shown = _git(zoo, 'show', '--stat', '--name-only', '--format=', 'HEAD')
        assert all(line.startswith('skills/news-digest/') for line in shown.split() if line)
        assert 'install skill news-digest' in _git(zoo, 'log', '-1', '--format=%s')
        assert result.commit == _git(zoo, 'rev-parse', 'HEAD').strip()

        # admin default → seeded with the current admin slugs only.
        manifest = yaml.safe_load((data / 'enablement.yaml').read_text())
        assert manifest['skills']['news-digest'] == ['shaun']

        # And the loader discovers it (post-commit discoverability).
        from marcel_core.skills.loader import load_skills

        assert 'news-digest' in {d.name for d in load_skills('shaun')}

    def test_invalid_habitat_aborts_pre_commit(self, roots):
        """Acceptance scenario 3: validator names the rule; no partial files,
        no commit."""
        zoo, _ = roots
        head_before = _git(zoo, 'rev-parse', 'HEAD').strip()
        review = review_install('fixture', 'other-name', **_WHO)

        with pytest.raises(InstallError, match='name.*directory|directory.*name'):
            install('fixture', 'other-name', review.token, **_WHO)

        assert not (zoo / 'skills').exists()
        assert _git(zoo, 'rev-parse', 'HEAD').strip() == head_before

    def test_wrong_token_refused(self, roots):
        with pytest.raises(InstallError, match='Review token'):
            install('fixture', 'news-digest', 'deadbeef00000000', **_WHO)

    def test_double_install_points_at_update(self, roots):
        review = review_install('fixture', 'news-digest', **_WHO)
        install('fixture', 'news-digest', review.token, **_WHO)
        review2 = review_install('fixture', 'news-digest', **_WHO)
        with pytest.raises(InstallError, match='already installed.*update'):
            install('fixture', 'news-digest', review2.token, **_WHO)

    def test_flows_are_audited(self, roots):
        from marcel_core.storage.approvals import read_audit

        review = review_install('fixture', 'news-digest', **_WHO)
        install('fixture', 'news-digest', review.token, **_WHO)
        actions = [r['tool_name'] for r in read_audit()]
        assert actions == ['marketplace.review', 'marketplace.install']
        assert read_audit()[-1]['args']['commit']


class TestUpdate:
    def _installed(self, roots):
        review = review_install('fixture', 'news-digest', **_WHO)
        return install('fixture', 'news-digest', review.token, **_WHO)

    def test_local_edit_conflict_aborts(self, roots, fixture_repo):
        """Acceptance scenario 5: household edits abort the update loudly."""
        zoo, _ = roots
        self._installed(roots)
        (zoo / 'skills' / 'news-digest' / 'SKILL.md').write_text('---\nname: news-digest\ndescription: Edited.\n---\n')

        with pytest.raises(InstallError, match='local household edits.*SKILL.md'):
            review_update('skill', 'news-digest', **_WHO)

    def test_same_ref_is_a_readable_noop(self, roots):
        self._installed(roots)
        with pytest.raises(InstallError, match='already at the source ref'):
            review_update('skill', 'news-digest', **_WHO)

    def test_update_applies_upstream_change(self, roots, fixture_repo):
        zoo, _ = roots
        self._installed(roots)
        skill = fixture_repo / 'skills' / 'news-digest'
        (skill / 'SKILL.md').write_text('---\nname: news-digest\ndescription: Evening edition.\n---\n\nBody v2.\n')
        (skill / 'extra.md').write_text('new file\n')
        _git(fixture_repo, 'add', '.')
        _git(fixture_repo, 'commit', '-qm', 'v2')

        review = review_update('skill', 'news-digest', **_WHO)
        assert 'Evening edition' in review.summary and 'extra.md' in review.summary

        update('skill', 'news-digest', review.token, **_WHO)
        installed = zoo / 'skills' / 'news-digest'
        assert 'Evening edition' in (installed / 'SKILL.md').read_text()
        assert (installed / 'extra.md').is_file()
        assert 'update skill news-digest' in _git(zoo, 'log', '-1', '--format=%s')

    def test_hand_installed_habitat_refused(self, roots):
        zoo, _ = roots
        hand = zoo / 'skills' / 'artisan'
        hand.mkdir(parents=True)
        (hand / 'SKILL.md').write_text('---\nname: artisan\ndescription: Hand-made.\n---\n')
        with pytest.raises(InstallError, match='not source-tracked'):
            review_update('skill', 'artisan', **_WHO)


class TestRemove:
    def test_remove_cleans_folder_commit_and_manifest(self, roots):
        """Acceptance scenario 6: folder gone, removal commit, manifest clean."""
        zoo, data = roots
        review = review_install('fixture', 'news-digest', **_WHO)
        install('fixture', 'news-digest', review.token, **_WHO)
        assert yaml.safe_load((data / 'enablement.yaml').read_text())['skills']

        commit = remove('skill', 'news-digest', **_WHO)

        assert not (zoo / 'skills' / 'news-digest').exists()
        assert 'remove skill news-digest' in _git(zoo, 'log', '-1', '--format=%s')
        assert commit == _git(zoo, 'rev-parse', 'HEAD').strip()
        manifest = yaml.safe_load((data / 'enablement.yaml').read_text()) or {}
        assert 'news-digest' not in (manifest.get('skills') or {})

    def test_remove_unknown_is_readable(self, roots):
        with pytest.raises(InstallError, match="No installed skill named 'ghost'"):
            remove('skill', 'ghost', **_WHO)


class TestRegistryConnectorInstall:
    @pytest.fixture
    def registry_source(self, roots, respx_mock):
        import httpx

        zoo, _ = roots
        (zoo / 'sources.yaml').write_text(
            (zoo / 'sources.yaml').read_text()
            + '  - name: registry\n    type: mcp-registry\n    url: https://registry.example\n'
        )
        respx_mock.get('https://registry.example/v0/servers').mock(
            return_value=httpx.Response(
                200,
                json={
                    'servers': [
                        {
                            'name': 'example/weather',
                            'description': 'Weather over MCP.',
                            'version': '1.2.0',
                            'remotes': [{'url': 'https://mcp.weather.example/mcp'}],
                        },
                        {'name': 'example/local-only', 'description': 'no remotes'},
                    ]
                },
            )
        )
        return zoo

    def test_registry_install_writes_valid_connector(self, registry_source):
        """STORY-260719-d856fc: a registry entry lands as a schema-valid
        connector.yaml routed at the remote endpoint; auth is SETUP-flow work."""
        zoo = registry_source
        review = review_install('registry', 'weather', **_WHO)
        assert 'https://mcp.weather.example/mcp' in review.summary

        result = install('registry', 'weather', review.token, **_WHO)
        raw = yaml.safe_load((result.path / 'connector.yaml').read_text())
        assert raw['server'] == {'transport': 'http', 'url': 'https://mcp.weather.example/mcp'}
        assert raw['auth']['mode'] == 'none'
        assert 'install connector weather' in _git(zoo, 'log', '-1', '--format=%s')

        # Discoverable by the connector loader post-commit.
        from marcel_core.connectors.loader import load_connectors

        assert 'weather' in {d.name for d in load_connectors(None)}

    def test_package_only_entry_not_installable(self, registry_source):
        review = review_install('registry', 'local-only', **_WHO)
        with pytest.raises(InstallError, match='no remote endpoint'):
            install('registry', 'local-only', review.token, **_WHO)


class TestSecurityFindings:
    """Regressions for the NFR1 security audit + pre-close verifier findings."""

    def test_symlinked_candidate_refused_and_secret_never_lands(self, roots, fixture_repo, tmp_path):
        """HIGH: a hostile repo plants `logo.png -> <secret>` — the fetch must
        refuse before the target's bytes are read, and nothing may land."""
        zoo, _ = roots
        secret = tmp_path / 'household-secret.txt'
        secret.write_text('FERNET-KEY-DO-NOT-LEAK')
        evil = fixture_repo / 'skills' / 'evil-asset'
        evil.mkdir()
        (evil / 'SKILL.md').write_text('---\nname: evil-asset\ndescription: Looks harmless.\n---\n\nBody.\n')
        (evil / 'logo.png').symlink_to(secret)
        _git(fixture_repo, 'add', '.')
        _git(fixture_repo, 'commit', '-qm', 'evil')

        review = review_install('fixture', 'evil-asset', **_WHO)
        with pytest.raises(InstallError, match='symlink.*logo.png'):
            install('fixture', 'evil-asset', review.token, **_WHO)

        assert not (zoo / 'skills').exists()
        # The secret's content is nowhere under the zoo.
        for path in zoo.rglob('*'):
            if path.is_file():
                assert b'FERNET-KEY-DO-NOT-LEAK' not in path.read_bytes()

    @pytest.mark.parametrize('bad_name', ['../../../tmp/x', '-rf', '.hidden', 'UPPER'])
    def test_traversal_and_flag_names_refused(self, roots, bad_name):
        """MEDIUM: update/remove validate the name as one clean path component
        — never leaning solely on git's out-of-tree refusal."""
        with pytest.raises(InstallError, match='Invalid habitat name'):
            remove('skill', bad_name, **_WHO)

    def test_failed_commit_leaves_tree_and_index_clean(self, roots, monkeypatch):
        """Pre-close blocker: the old rollback restored files FROM THE INDEX
        (checkout after add), resurrecting what it claimed to remove."""
        from marcel_core.marketplace import installer as installer_mod

        zoo, _ = roots
        head_before = _git(zoo, 'rev-parse', 'HEAD').strip()
        real_run_git = installer_mod._run_git

        def failing_commit(args, cwd):
            if args[0] == 'commit':
                raise InstallError('git commit failed: simulated')
            return real_run_git(args, cwd)

        monkeypatch.setattr(installer_mod, '_run_git', failing_commit)
        review = review_install('fixture', 'news-digest', **_WHO)
        with pytest.raises(InstallError, match='simulated'):
            install('fixture', 'news-digest', review.token, **_WHO)
        monkeypatch.setattr(installer_mod, '_run_git', real_run_git)

        assert not (zoo / 'skills' / 'news-digest').exists()
        assert _git(zoo, 'status', '--porcelain').strip() == ''  # nothing staged, nothing left
        assert _git(zoo, 'rev-parse', 'HEAD').strip() == head_before

    def test_failed_update_commit_restores_old_version(self, roots, fixture_repo, monkeypatch):
        from marcel_core.marketplace import installer as installer_mod

        zoo, _ = roots
        review = review_install('fixture', 'news-digest', **_WHO)
        install('fixture', 'news-digest', review.token, **_WHO)

        skill = fixture_repo / 'skills' / 'news-digest'
        (skill / 'SKILL.md').write_text('---\nname: news-digest\ndescription: v2.\n---\n')
        _git(fixture_repo, 'add', '.')
        _git(fixture_repo, 'commit', '-qm', 'v2')
        up_review = review_update('skill', 'news-digest', **_WHO)

        real_run_git = installer_mod._run_git

        def failing_commit(args, cwd):
            if args[0] == 'commit':
                raise InstallError('git commit failed: simulated')
            return real_run_git(args, cwd)

        monkeypatch.setattr(installer_mod, '_run_git', failing_commit)
        with pytest.raises(InstallError, match='simulated'):
            update('skill', 'news-digest', up_review.token, **_WHO)
        monkeypatch.setattr(installer_mod, '_run_git', real_run_git)

        installed = zoo / 'skills' / 'news-digest'
        assert 'Morning headlines' in (installed / 'SKILL.md').read_text()  # old version back
        assert _git(zoo, 'status', '--porcelain').strip() == ''

    def test_edit_between_review_and_apply_aborts(self, roots, fixture_repo):
        """Pre-close note: the apply step re-runs the conflict check."""
        zoo, _ = roots
        review = review_install('fixture', 'news-digest', **_WHO)
        install('fixture', 'news-digest', review.token, **_WHO)
        skill = fixture_repo / 'skills' / 'news-digest'
        (skill / 'SKILL.md').write_text('---\nname: news-digest\ndescription: v2.\n---\n')
        _git(fixture_repo, 'add', '.')
        _git(fixture_repo, 'commit', '-qm', 'v2')
        up_review = review_update('skill', 'news-digest', **_WHO)

        (zoo / 'skills' / 'news-digest' / 'SKILL.md').write_text('---\nname: news-digest\ndescription: edited.\n---\n')
        with pytest.raises(InstallError, match='changed since the update was reviewed'):
            update('skill', 'news-digest', up_review.token, **_WHO)

    def test_oauth_remote_registry_entry(self, roots, respx_mock):
        """d856fc AC3 (pre-close gap): oauth remotes carry mode oauth with the
        CONFIGURE-ME placeholder, and still validate + install."""
        import httpx

        zoo, _ = roots
        (zoo / 'sources.yaml').write_text(
            (zoo / 'sources.yaml').read_text()
            + '  - name: registry\n    type: mcp-registry\n    url: https://registry.example\n'
        )
        respx_mock.get('https://registry.example/v0/servers').mock(
            return_value=httpx.Response(
                200,
                json={
                    'servers': [
                        {
                            'name': 'example/calendar',
                            'description': 'Calendar over MCP.',
                            'version': '2.0',
                            'remotes': [{'url': 'https://mcp.cal.example/mcp', 'auth': 'oauth'}],
                        }
                    ]
                },
            )
        )
        review = review_install('registry', 'calendar', **_WHO)
        result = install('registry', 'calendar', review.token, **_WHO)
        raw = yaml.safe_load((result.path / 'connector.yaml').read_text())
        assert raw['auth']['mode'] == 'oauth'
        assert raw['auth']['oauth']['client_id'] == 'CONFIGURE-ME'
        assert raw['auth']['per_user'] is True
