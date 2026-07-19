"""Tests for the agentskills.io-conformant skill loader (FEAT-260718-85b545).

Covers frontmatter parsing + validation, the ``metadata`` marcel-* extension
map, legacy-frontmatter migration, the SETUP.md requirement fallback, the
three-root scoping chain (data-user → zoo-user → zoo-global), role gating, and
skill-root-scoped resource access.
"""

from __future__ import annotations

import logging

import pytest

from marcel_core.skills.loader import (
    _coerce_metadata,
    _connector_requirements_met,
    _credentials_present,
    _csv,
    _env_present,
    _find_skill_dir,
    _load_skill_dir,
    _migrate_legacy_frontmatter,
    _parse_frontmatter,
    _requirements_met,
    _skill_dirs,
    get_skill_content,
    get_skill_resource,
    list_skill_resources,
    load_skills,
    validate_skill_frontmatter,
)
from marcel_core.toolkit import ToolkitMetadata


@pytest.fixture
def isolated_metadata(monkeypatch):
    """Provide a clean toolkit metadata registry for the test."""
    from marcel_core.toolkit import _metadata

    saved = dict(_metadata)
    _metadata.clear()
    yield _metadata
    _metadata.clear()
    _metadata.update(saved)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Set up zoo + data roots and return a helper that writes a skill into a scope.

    ``make(scope, name, skill_md=..., setup_md=..., files=..., user=...)`` writes a
    skill directory under one of the three scopes ('zoo-global', 'zoo-user',
    'data-user') and returns its path.
    """
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    data = tmp_path / 'data'
    (zoo / 'skills').mkdir(parents=True)
    (data / 'skills').mkdir(parents=True)
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data))

    def make(scope, name, *, skill_md=None, setup_md=None, files=None, user='shaun'):
        if scope == 'zoo-global':
            base = zoo / 'skills' / name
        elif scope == 'zoo-user':
            base = zoo / 'users' / user / 'skills' / name
        elif scope == 'data-user':
            base = data / 'users' / user / 'skills' / name
        else:
            raise ValueError(f'unknown scope {scope!r}')
        base.mkdir(parents=True, exist_ok=True)
        if skill_md is not None:
            (base / 'SKILL.md').write_text(skill_md)
        if setup_md is not None:
            (base / 'SETUP.md').write_text(setup_md)
        for fname, content in (files or {}).items():
            (base / fname).write_text(content)
        return base

    return make


def _md(name, description='A test skill', body='Body content.', *, metadata=None, extra_fm=None):
    """Assemble a conformant SKILL.md string."""
    lines = ['---', f'name: {name}', f'description: {description}']
    for key, value in (extra_fm or {}).items():
        lines.append(f'{key}: {value}')
    if metadata:
        lines.append('metadata:')
        for key, value in metadata.items():
            lines.append(f'  {key}: {value}')
    lines.append('---')
    return '\n'.join(lines) + f'\n\n{body}'


# ---------------------------------------------------------------------------
# frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        fm, body = _parse_frontmatter('---\nname: test\ndescription: A test skill\n---\n\nBody content here.')
        assert fm['name'] == 'test'
        assert fm['description'] == 'A test skill'
        assert body == 'Body content here.'

    def test_no_frontmatter(self):
        fm, body = _parse_frontmatter('Just some body text.')
        assert fm == {}
        assert body == 'Just some body text.'

    def test_empty_frontmatter(self):
        fm, body = _parse_frontmatter('---\n---\n\nBody.')
        assert fm == {}
        assert body == 'Body.'

    def test_no_closing_delimiter(self):
        fm, body = _parse_frontmatter('---\nname: test\n\nBody without closing.')
        assert fm == {}

    def test_invalid_yaml_returns_empty_fm(self):
        fm, body = _parse_frontmatter('---\n{invalid: yaml: ::\n---\n\nBody.')
        assert fm == {}
        assert 'Body.' in body

    def test_non_dict_frontmatter_returns_empty(self):
        fm, body = _parse_frontmatter('---\n- just\n- a\n- list\n---\n\nBody.')
        assert fm == {}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestValidateFrontmatter:
    def test_conformant_returns_none(self):
        assert validate_skill_frontmatter({'name': 'news', 'description': 'x'}, 'news') is None

    def test_missing_name(self):
        err = validate_skill_frontmatter({'description': 'x'}, 'news')
        assert err is not None and 'name' in err

    def test_name_not_string(self):
        err = validate_skill_frontmatter({'name': 5, 'description': 'x'}, 'news')
        assert err is not None and 'name' in err

    @pytest.mark.parametrize('bad', ['News', 'has_underscore', '-lead', 'trail-', 'double--hyphen', 'sp ace'])
    def test_invalid_name_regex(self, bad):
        err = validate_skill_frontmatter({'name': bad, 'description': 'x'}, bad)
        assert err is not None and 'name' in err

    def test_name_too_long(self):
        long = 'a' * 65
        err = validate_skill_frontmatter({'name': long, 'description': 'x'}, long)
        assert err is not None and 'name' in err

    def test_name_must_equal_dir(self):
        err = validate_skill_frontmatter({'name': 'news', 'description': 'x'}, 'weather')
        assert err is not None and 'directory name' in err

    def test_missing_description(self):
        err = validate_skill_frontmatter({'name': 'news'}, 'news')
        assert err is not None and 'description' in err

    def test_description_too_long(self):
        err = validate_skill_frontmatter({'name': 'news', 'description': 'x' * 1025}, 'news')
        assert err is not None and 'description' in err


# ---------------------------------------------------------------------------
# metadata coercion + legacy migration
# ---------------------------------------------------------------------------


class TestCoerceMetadata:
    def test_none_returns_empty(self):
        assert _coerce_metadata({}, 'news') == {}

    def test_non_dict_ignored_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            assert _coerce_metadata({'metadata': ['not', 'a', 'map']}, 'news') == {}
        assert any('metadata must be a map' in r.getMessage() for r in caplog.records)

    def test_coerces_values_to_str(self):
        out = _coerce_metadata({'metadata': {'marcel-tier': 'fast', 'count': 3}}, 'news')
        assert out == {'marcel-tier': 'fast', 'count': '3'}


class TestMigrateLegacy:
    def test_depends_on_list(self, caplog):
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            out = _migrate_legacy_frontmatter({'depends_on': ['docker', 'icloud']}, 'x')
        assert out['marcel-connectors'] == 'docker,icloud'
        assert any('depends_on' in r.getMessage() for r in caplog.records)

    def test_depends_on_string(self):
        assert _migrate_legacy_frontmatter({'depends_on': 'docker'}, 'x')['marcel-connectors'] == 'docker'

    def test_preferred_tier(self):
        assert _migrate_legacy_frontmatter({'preferred_tier': 'power'}, 'x')['marcel-tier'] == 'power'

    def test_requires_role(self):
        assert _migrate_legacy_frontmatter({'requires': {'role': 'admin'}}, 'x')['marcel-role'] == 'admin'

    def test_requires_credentials_and_env(self):
        out = _migrate_legacy_frontmatter({'requires': {'credentials': ['K'], 'env': ['E']}}, 'x')
        assert out['marcel-requires-credentials'] == 'K'
        assert out['marcel-requires-env'] == 'E'

    def test_native_metadata_wins_over_legacy(self, tmp_path, roots):
        # Both native metadata and legacy preferred_tier present — native wins.
        roots(
            'zoo-global',
            'dual',
            skill_md=_md('dual', metadata={'marcel-tier': 'fast'}, extra_fm={'preferred_tier': 'power'}),
        )
        doc = load_skills('shaun')[0]
        assert doc.preferred_tier == 'fast'


class TestCsv:
    def test_splits_and_strips(self):
        assert _csv(' a, b ,c ') == ['a', 'b', 'c']

    def test_none_and_empty(self):
        assert _csv(None) == []
        assert _csv('') == []


# ---------------------------------------------------------------------------
# requirement checks (SETUP.md fallback drivers)
# ---------------------------------------------------------------------------


class TestRequirementChecks:
    def test_env_present(self, monkeypatch):
        monkeypatch.setenv('SKILL_VAR', 'v')
        assert _env_present(['SKILL_VAR']) is True

    def test_env_absent(self, monkeypatch):
        monkeypatch.delenv('SKILL_VAR', raising=False)
        assert _env_present(['SKILL_VAR']) is False

    def test_no_env_keys_passes(self):
        assert _env_present([]) is True

    def test_credentials_present(self, monkeypatch):
        monkeypatch.setattr('marcel_core.storage.credentials.load_credentials', lambda slug: {'MY_KEY': 'secret'})
        assert _credentials_present(['MY_KEY'], 'shaun') is True

    def test_credentials_absent(self, monkeypatch):
        monkeypatch.setattr('marcel_core.storage.credentials.load_credentials', lambda slug: {})
        assert _credentials_present(['MY_KEY'], 'shaun') is False

    def test_no_credential_keys_passes(self):
        assert _credentials_present([], 'shaun') is True

    def test_credential_load_exception_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.storage.credentials.load_credentials',
            lambda slug: (_ for _ in ()).throw(RuntimeError('disk error')),
        )
        assert _credentials_present(['K'], 'shaun') is False

    def test_requirements_met_all_green(self, monkeypatch):
        monkeypatch.setenv('E', 'v')
        monkeypatch.setattr('marcel_core.storage.credentials.load_credentials', lambda slug: {'K': 'secret'})
        meta = {'marcel-requires-env': 'E', 'marcel-requires-credentials': 'K'}
        assert _requirements_met(meta, [], 'shaun') is True

    def test_requirements_unmet_on_missing_env(self, monkeypatch):
        monkeypatch.delenv('E', raising=False)
        assert _requirements_met({'marcel-requires-env': 'E'}, [], 'shaun') is False


class TestConnectorRequirements:
    def test_no_connectors_passes(self, isolated_metadata):
        assert _connector_requirements_met([], 'shaun') is True

    def test_unregistered_connector_unmet(self, isolated_metadata):
        assert _connector_requirements_met(['docker'], 'shaun') is False

    def test_registered_no_requires_passes(self, isolated_metadata):
        isolated_metadata['docker'] = ToolkitMetadata(name='docker', requires={})
        assert _connector_requirements_met(['docker'], 'shaun') is True

    def test_env_requirement_propagates(self, isolated_metadata, monkeypatch):
        isolated_metadata['docker'] = ToolkitMetadata(name='docker', requires={'env': ['DOCKER_HOST']})
        monkeypatch.delenv('DOCKER_HOST', raising=False)
        assert _connector_requirements_met(['docker'], 'shaun') is False
        monkeypatch.setenv('DOCKER_HOST', 'unix:///x')
        assert _connector_requirements_met(['docker'], 'shaun') is True

    def test_installed_package_passes(self, isolated_metadata):
        isolated_metadata['pkgs'] = ToolkitMetadata(name='pkgs', requires={'packages': ['pytest']})
        assert _connector_requirements_met(['pkgs'], 'shaun') is True

    def test_missing_package_fails(self, isolated_metadata):
        isolated_metadata['pkgs'] = ToolkitMetadata(name='pkgs', requires={'packages': ['nonexistent_xyz_123']})
        assert _connector_requirements_met(['pkgs'], 'shaun') is False

    def test_file_requirement(self, isolated_metadata, tmp_path, monkeypatch):
        monkeypatch.setattr('marcel_core.storage._root._DATA_ROOT', tmp_path)
        isolated_metadata['keyed'] = ToolkitMetadata(name='keyed', requires={'files': ['key.pem']})
        assert _connector_requirements_met(['keyed'], 'shaun') is False
        user_dir = tmp_path / 'users' / 'shaun'
        user_dir.mkdir(parents=True)
        (user_dir / 'key.pem').write_text('k')
        assert _connector_requirements_met(['keyed'], 'shaun') is True


# ---------------------------------------------------------------------------
# _load_skill_dir
# ---------------------------------------------------------------------------


class TestLoadSkillDir:
    def test_conformant_loads(self, tmp_path):
        d = tmp_path / 'news'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('news', 'News digest', 'How to read news.'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert doc.name == 'news'
        assert doc.description == 'News digest'
        assert doc.content == 'How to read news.'
        assert doc.is_setup is False
        assert doc.source == 'zoo-global'

    def test_no_skill_md_returns_none(self, tmp_path):
        d = tmp_path / 'setup-only'
        d.mkdir()
        (d / 'SETUP.md').write_text(_md('setup-only'))
        assert _load_skill_dir(d, 'zoo-global', 'shaun') is None

    def test_nonconformant_name_returns_none(self, tmp_path):
        # name missing → no more dirname default; validation rejects it.
        d = tmp_path / 'my-skill'
        d.mkdir()
        (d / 'SKILL.md').write_text('---\ndescription: no name\n---\n\nBody.')
        assert _load_skill_dir(d, 'zoo-global', 'shaun') is None

    def test_name_mismatch_returns_none(self, tmp_path):
        d = tmp_path / 'weather'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('news'))
        assert _load_skill_dir(d, 'zoo-global', 'shaun') is None

    def test_setup_fallback_when_requirements_unmet(self, tmp_path, monkeypatch):
        monkeypatch.delenv('MISSING', raising=False)
        d = tmp_path / 'gated'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('gated', metadata={'marcel-requires-env': 'MISSING'}, body='Full skill.'))
        (d / 'SETUP.md').write_text(_md('gated', 'Setup guide', 'How to set up.'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert doc.is_setup is True
        assert doc.content == 'How to set up.'

    def test_requirements_met_serves_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv('PRESENT', 'yes')
        d = tmp_path / 'gated'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('gated', metadata={'marcel-requires-env': 'PRESENT'}, body='Full skill.'))
        (d / 'SETUP.md').write_text(_md('gated', 'Setup', 'Setup guide.'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert doc.is_setup is False
        assert doc.content == 'Full skill.'

    def test_requirements_unmet_no_setup_serves_skill(self, tmp_path, monkeypatch):
        monkeypatch.delenv('MISSING', raising=False)
        d = tmp_path / 'gated'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('gated', metadata={'marcel-requires-env': 'MISSING'}, body='Content.'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert doc.is_setup is False
        assert doc.content == 'Content.'

    def test_marcel_tier_parsed(self, tmp_path):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-tier': 'fast'}))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.preferred_tier == 'fast'

    def test_invalid_tier_dropped_with_warning(self, tmp_path, caplog):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-tier': 'backup'}))
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.preferred_tier is None
        assert any('marcel-tier' in r.getMessage() for r in caplog.records)

    def test_default_enabled_absent_defaults_to_all(self, tmp_path):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.default_enabled == 'all'

    @pytest.mark.parametrize('value', ['all', 'admin', 'none'])
    def test_default_enabled_valid_values_parsed(self, tmp_path, value):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-default-enabled': value}))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.default_enabled == value

    def test_invalid_default_enabled_falls_back_with_warning(self, tmp_path, caplog):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-default-enabled': 'everyone'}))
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.default_enabled == 'all'
        assert any('marcel-default-enabled' in r.getMessage() for r in caplog.records)

    def test_local_tier_is_legal(self, tmp_path):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-tier': 'local'}))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.preferred_tier == 'local'

    def test_marcel_role_parsed(self, tmp_path):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-role': 'admin'}))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.role == 'admin'

    def test_invalid_role_dropped_with_warning(self, tmp_path, caplog):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', metadata={'marcel-role': 'wizard'}))
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.role is None
        assert any('marcel-role' in r.getMessage() for r in caplog.records)

    def test_setup_fallback_drops_tier(self, tmp_path, monkeypatch):
        monkeypatch.delenv('MISSING', raising=False)
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(
            _md('s', metadata={'marcel-tier': 'power', 'marcel-requires-env': 'MISSING'}, body='Full.')
        )
        (d / 'SETUP.md').write_text(_md('s', 'Setup', 'Setup.'))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None and doc.is_setup is True
        assert doc.preferred_tier is None

    def test_components_loaded(self, tmp_path):
        d = tmp_path / 'ui'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('ui'))
        (d / 'components.yaml').write_text(
            'components:\n  - name: card\n    description: A card\n    props:\n      type: object\n'
        )
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert [c.name for c in doc.components] == ['card']

    def test_long_description_warns(self, tmp_path, caplog):
        d = tmp_path / 's'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('s', description='x' * 600))
        with caplog.at_level(logging.WARNING, logger='marcel_core.skills.loader'):
            doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert any('catalog entries should stay compact' in r.getMessage() for r in caplog.records)

    def test_connector_credentials_aggregated(self, tmp_path, isolated_metadata):
        isolated_metadata['banking'] = ToolkitMetadata(name='banking', requires={'credentials': ['BANK_API_KEY']})
        d = tmp_path / 'banking'
        d.mkdir()
        (d / 'SKILL.md').write_text(_md('banking', metadata={'marcel-connectors': 'banking'}))
        doc = _load_skill_dir(d, 'zoo-global', 'shaun')
        assert doc is not None
        assert 'BANK_API_KEY' in doc.credential_keys


# ---------------------------------------------------------------------------
# scoping chain
# ---------------------------------------------------------------------------


class TestScopingChain:
    def test_no_user_only_global(self, roots):
        dirs = _skill_dirs(None)
        assert [src for _p, src in dirs] == ['zoo-global']

    def test_all_three_roots_present(self, roots):
        roots('zoo-user', 'a', skill_md=_md('a'))
        roots('data-user', 'b', skill_md=_md('b'))
        sources = [src for _p, src in _skill_dirs('shaun')]
        assert sources == ['zoo-global', 'zoo-user', 'data-user']

    def test_missing_roots_absent(self, roots):
        # Only zoo-global exists (roots fixture makes it); no per-user dirs.
        sources = [src for _p, src in _skill_dirs('shaun')]
        assert sources == ['zoo-global']

    def test_most_specific_wins_with_shadow_log(self, roots, caplog):
        roots('zoo-global', 'news', skill_md=_md('news', body='Global.'))
        roots('data-user', 'news', skill_md=_md('news', body='User override.'))
        with caplog.at_level(logging.INFO, logger='marcel_core.skills.loader'):
            docs = load_skills('shaun')
        news = next(d for d in docs if d.name == 'news')
        assert news.source == 'data-user'
        assert news.content == 'User override.'
        assert any('shadows' in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# role gating
# ---------------------------------------------------------------------------


class TestRoleGating:
    def test_admin_skill_hidden_from_user(self, roots):
        roots('zoo-global', 'devtool', skill_md=_md('devtool', metadata={'marcel-role': 'admin'}))
        roots('zoo-global', 'news', skill_md=_md('news'))
        names = [d.name for d in load_skills('shaun', role='user')]
        assert 'devtool' not in names
        assert 'news' in names

    def test_admin_skill_visible_to_admin(self, roots):
        roots('zoo-global', 'devtool', skill_md=_md('devtool', metadata={'marcel-role': 'admin'}))
        names = [d.name for d in load_skills('shaun', role='admin')]
        assert 'devtool' in names

    def test_default_role_is_user(self, roots):
        roots('zoo-global', 'devtool', skill_md=_md('devtool', metadata={'marcel-role': 'admin'}))
        assert [d.name for d in load_skills('shaun')] == []


# ---------------------------------------------------------------------------
# load_skills discovery
# ---------------------------------------------------------------------------


class TestLoadSkills:
    def test_sorted_by_name(self, roots):
        roots('zoo-global', 'zebra', skill_md=_md('zebra'))
        roots('zoo-global', 'alpha', skill_md=_md('alpha'))
        assert [d.name for d in load_skills('shaun')] == ['alpha', 'zebra']

    def test_hidden_and_underscore_dirs_skipped(self, roots):
        roots('zoo-global', '.hidden', skill_md=_md('hidden'))
        roots('zoo-global', '_internal', skill_md=_md('internal'))
        roots('zoo-global', 'valid', skill_md=_md('valid'))
        assert [d.name for d in load_skills('shaun')] == ['valid']

    def test_empty_dir_skipped(self, roots):
        (roots('zoo-global', 'valid', skill_md=_md('valid')).parent / 'empty').mkdir()
        assert [d.name for d in load_skills('shaun')] == ['valid']


# ---------------------------------------------------------------------------
# get_skill_content
# ---------------------------------------------------------------------------


class TestGetSkillContent:
    def test_returns_body(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news', body='News body.'))
        assert get_skill_content('news', 'shaun') == 'News body.'

    def test_unknown_returns_none(self, roots):
        assert get_skill_content('ghost', 'shaun') is None

    def test_role_gated_content(self, roots):
        roots('zoo-global', 'devtool', skill_md=_md('devtool', metadata={'marcel-role': 'admin'}, body='D.'))
        assert get_skill_content('devtool', 'shaun', role='user') is None
        assert get_skill_content('devtool', 'shaun', role='admin') == 'D.'


# ---------------------------------------------------------------------------
# resources (skill-root-scoped)
# ---------------------------------------------------------------------------


class TestResources:
    def test_resource_by_exact_filename(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'), files={'feeds.yaml': 'feeds: []'})
        assert get_skill_resource('news', 'feeds.yaml', 'shaun') == 'feeds: []'

    def test_resource_by_stem(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'), files={'feeds.yaml': 'feeds: []'})
        assert get_skill_resource('news', 'feeds', 'shaun') == 'feeds: []'

    def test_resource_case_insensitive(self, roots):
        roots('zoo-global', 'banking', skill_md=_md('banking'), files={'SETUP.md': '## Setup'})
        assert get_skill_resource('banking', 'setup', 'shaun') == '## Setup'

    def test_skill_md_not_exposed(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'))
        assert get_skill_resource('news', 'SKILL.md', 'shaun') is None
        assert get_skill_resource('news', 'SKILL', 'shaun') is None

    def test_unknown_skill_returns_none(self, roots):
        assert get_skill_resource('ghost', 'feeds', 'shaun') is None

    def test_missing_resource_returns_none(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'))
        assert get_skill_resource('news', 'feeds', 'shaun') is None

    def test_traversal_name_does_not_escape(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'), files={'feeds.yaml': 'x'})
        # A traversal-flavoured name has no matching child → None (never escapes root).
        assert get_skill_resource('news', '../../etc/passwd', 'shaun') is None

    def test_list_resources(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'), files={'SETUP.md': 's', 'feeds.yaml': 'f'})
        resources = list_skill_resources('news', 'shaun')
        assert 'SETUP.md' in resources
        assert 'feeds.yaml' in resources
        assert 'SKILL.md' not in resources

    def test_list_resources_unknown_skill(self, roots):
        assert list_skill_resources('ghost', 'shaun') == []

    def test_find_skill_dir_most_specific(self, roots):
        roots('zoo-global', 'news', skill_md=_md('news'))
        data_dir = roots('data-user', 'news', skill_md=_md('news'))
        assert _find_skill_dir('news', 'shaun') == data_dir


class TestConnectorPairedSkill:
    """A skill naming a connector habitat must serve SKILL.md, not SETUP.md.

    Regression for the pre-close finding: `marcel-connectors` was resolved only
    against toolkit metadata, so a connector name returned None and the skill
    was treated as unconfigured — a linked user got the setup page instead of
    the skill body, forever. The SETUP.md in these fixtures is the point: the
    original test missed this because without one, `_load_skill_dir` short-
    circuits on `not setup_md.exists()` and never consults requirements.
    """

    def _connector(self, roots, name='weather'):
        base = roots('zoo-global', name, skill_md=None)  # reuse the root helper's zoo dir
        return base

    def test_connector_backed_skill_serves_the_skill_body(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        (zoo / 'connectors' / 'weather').mkdir(parents=True)
        (zoo / 'connectors' / 'weather' / 'connector.yaml').write_text(
            'name: weather\ndescription: Weather\n'
            'server: {transport: http, url: https://mcp.test}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        sdir = zoo / 'skills' / 'forecast'
        sdir.mkdir(parents=True)
        (sdir / 'SKILL.md').write_text(
            '---\nname: forecast\ndescription: Weather talk\n'
            'metadata:\n  marcel-connectors: weather\n---\n\nSKILL BODY.'
        )
        (sdir / 'SETUP.md').write_text('---\nname: forecast\ndescription: Setup\n---\n\nSETUP BODY.')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        doc = next(d for d in load_skills('shaun') if d.name == 'forecast')
        assert doc.is_setup is False
        assert doc.content == 'SKILL BODY.'

    def test_unknown_connector_name_still_falls_back_to_setup(self, tmp_path, monkeypatch):
        """A name that is neither a connector nor a toolkit remains unmet."""
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        sdir = zoo / 'skills' / 'forecast'
        sdir.mkdir(parents=True)
        (sdir / 'SKILL.md').write_text(
            '---\nname: forecast\ndescription: Weather talk\nmetadata:\n  marcel-connectors: ghost\n---\n\nSKILL BODY.'
        )
        (sdir / 'SETUP.md').write_text('---\nname: forecast\ndescription: Setup\n---\n\nSETUP BODY.')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        doc = next(d for d in load_skills('shaun') if d.name == 'forecast')
        assert doc.is_setup is True
        assert doc.content == 'SETUP BODY.'
