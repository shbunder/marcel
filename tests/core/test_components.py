"""Tests for A2UI component schema parsing, registry, and artifact integration."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marcel_core.skills.component_registry import ComponentRegistry, build_registry
from marcel_core.skills.components import ComponentPayload, ComponentSchema, parse_components_yaml
from marcel_core.storage import _root
from marcel_core.storage.artifacts import create_artifact, load_artifact

# ---------------------------------------------------------------------------
# parse_components_yaml
# ---------------------------------------------------------------------------


class TestParseComponentsYaml:
    def test_valid_yaml(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text(
            'components:\n'
            '  - name: widget_a\n'
            '    description: A test widget\n'
            '    props:\n'
            '      type: object\n'
            '      properties:\n'
            '        title:\n'
            '          type: string\n'
        )
        schemas = parse_components_yaml(yaml_file, 'test-skill')
        assert len(schemas) == 1
        assert schemas[0].name == 'widget_a'
        assert schemas[0].description == 'A test widget'
        assert schemas[0].skill == 'test-skill'
        assert schemas[0].props['type'] == 'object'

    def test_multiple_components(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text(
            'components:\n'
            '  - name: alpha\n'
            '    description: First\n'
            '    props: {}\n'
            '  - name: beta\n'
            '    description: Second\n'
            '    props:\n'
            '      type: object\n'
        )
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert len(schemas) == 2
        assert schemas[0].name == 'alpha'
        assert schemas[1].name == 'beta'

    def test_missing_file(self, tmp_path):
        missing = tmp_path / 'does-not-exist.yaml'
        schemas = parse_components_yaml(missing, 'skill')
        assert schemas == []

    def test_invalid_yaml(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text('{invalid: yaml: ::')
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert schemas == []

    def test_not_a_dict(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text('- just a list')
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert schemas == []

    def test_components_not_a_list(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text('components: not-a-list')
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert schemas == []

    def test_entry_without_name_skipped(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text(
            'components:\n  - description: no name field\n    props: {}\n  - name: valid\n    props: {}\n'
        )
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert len(schemas) == 1
        assert schemas[0].name == 'valid'

    def test_empty_components_list(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text('components: []')
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert schemas == []

    def test_defaults_for_optional_fields(self, tmp_path):
        yaml_file = tmp_path / 'components.yaml'
        yaml_file.write_text('components:\n  - name: minimal\n')
        schemas = parse_components_yaml(yaml_file, 'skill')
        assert len(schemas) == 1
        assert schemas[0].description == ''
        assert schemas[0].props == {}


# ---------------------------------------------------------------------------
# ComponentSchema model
# ---------------------------------------------------------------------------


class TestComponentSchema:
    def test_create(self):
        schema = ComponentSchema(name='test', description='desc', skill='s', props={'type': 'object'})
        assert schema.name == 'test'
        assert schema.props == {'type': 'object'}


class TestComponentPayload:
    def test_create(self):
        payload = ComponentPayload(component='test', props={'key': 'value'})
        assert payload.component == 'test'
        assert payload.props == {'key': 'value'}


# ---------------------------------------------------------------------------
# ComponentRegistry
# ---------------------------------------------------------------------------


class TestComponentRegistry:
    def test_empty_registry(self):
        registry = ComponentRegistry([])
        assert len(registry) == 0
        assert registry.list_all() == []
        assert registry.get('anything') is None

    def test_single_component(self):
        c = ComponentSchema(name='widget', skill='s')
        registry = ComponentRegistry([c])
        assert len(registry) == 1
        assert registry.get('widget') is not None
        assert registry.get('widget') == c

    def test_multiple_components(self):
        components = [
            ComponentSchema(name='a', skill='s1'),
            ComponentSchema(name='b', skill='s2'),
        ]
        registry = ComponentRegistry(components)
        assert len(registry) == 2
        assert registry.get('a') is not None
        assert registry.get('b') is not None

    def test_name_collision_last_wins(self):
        c1 = ComponentSchema(name='dup', skill='first')
        c2 = ComponentSchema(name='dup', skill='second')
        registry = ComponentRegistry([c1, c2])
        assert len(registry) == 1
        assert registry.get('dup') is not None
        assert registry.get('dup').skill == 'second'  # type: ignore[union-attr]

    def test_list_all(self):
        components = [
            ComponentSchema(name='x', skill='s'),
            ComponentSchema(name='y', skill='s'),
        ]
        registry = ComponentRegistry(components)
        names = [c.name for c in registry.list_all()]
        assert 'x' in names
        assert 'y' in names


# ---------------------------------------------------------------------------
# build_registry integration
# ---------------------------------------------------------------------------


class TestBuildRegistry:
    def test_build_with_components(self, tmp_path, monkeypatch):
        import marcel_core.skills.loader as loader

        skills_dir = tmp_path / 'skills'
        skills_dir.mkdir(parents=True)

        # Create a skill with components
        skill_a = skills_dir / 'alpha'
        skill_a.mkdir()
        (skill_a / 'SKILL.md').write_text('---\nname: alpha\ndescription: Test\n---\n\nBody.')
        (skill_a / 'components.yaml').write_text(
            'components:\n  - name: alpha_widget\n    description: Alpha widget\n    props:\n      type: object\n'
        )

        # Create a skill without components
        skill_b = skills_dir / 'beta'
        skill_b.mkdir()
        (skill_b / 'SKILL.md').write_text('---\nname: beta\n---\n\nBody.')

        monkeypatch.setattr(loader, '_skills_dir', lambda: skills_dir)

        registry = build_registry('user')
        assert len(registry) == 1
        assert registry.get('alpha_widget') is not None
        assert registry.get('alpha_widget').skill == 'alpha'  # type: ignore[union-attr]

    def test_build_empty(self, tmp_path, monkeypatch):
        import marcel_core.skills.loader as loader

        monkeypatch.setattr(loader, '_skills_dir', lambda: tmp_path / 'nonexistent')

        registry = build_registry('user')
        assert len(registry) == 0


# ---------------------------------------------------------------------------
# Artifact model with a2ui content type
# ---------------------------------------------------------------------------


class TestA2UIArtifacts:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

    def test_create_a2ui_artifact(self):
        artifact_id = create_artifact(
            user_slug='alice',
            conversation_id='conv-1',
            content_type='a2ui',
            content='{"events": [{"date": "2026-04-11", "title": "Test"}]}',
            title='Calendar View',
            component_name='calendar',
        )
        loaded = load_artifact(artifact_id)
        assert loaded is not None
        assert loaded.content_type == 'a2ui'
        assert loaded.component_name == 'calendar'

    def test_a2ui_artifact_without_component_name(self):
        artifact_id = create_artifact(
            user_slug='alice',
            conversation_id='conv-1',
            content_type='a2ui',
            content='{}',
            title='Unknown Component',
        )
        loaded = load_artifact(artifact_id)
        assert loaded is not None
        assert loaded.content_type == 'a2ui'
        assert loaded.component_name is None

    def test_legacy_artifact_no_component_name(self):
        artifact_id = create_artifact(
            user_slug='alice',
            conversation_id='conv-1',
            content_type='markdown',
            content='# Hello',
            title='Regular',
        )
        loaded = load_artifact(artifact_id)
        assert loaded is not None
        assert loaded.component_name is None


# ---------------------------------------------------------------------------
# Skill loader components integration
# ---------------------------------------------------------------------------


class TestFormatComponentsCatalog:
    def test_empty_returns_empty_string(self):
        from marcel_core.skills.loader import format_components_catalog

        assert format_components_catalog([]) == ''

    def test_skill_without_components_omitted(self, tmp_path):
        from marcel_core.skills.loader import SkillDoc, format_components_catalog

        skill = SkillDoc(name='a', description='', content='', is_setup=False, source='data')
        assert format_components_catalog([skill]) == ''

    def test_single_component_formatted(self):
        from marcel_core.skills.loader import SkillDoc, format_components_catalog

        component = ComponentSchema(
            name='transaction_list',
            description='List of bank transactions',
            skill='banking',
            props={'type': 'object', 'properties': {'transactions': {}, 'currency': {}}},
        )
        skill = SkillDoc(
            name='banking',
            description='Banking',
            content='',
            is_setup=False,
            source='data',
            components=[component],
        )
        result = format_components_catalog([skill])
        assert '**transaction_list**' in result
        assert '(banking)' in result
        assert 'List of bank transactions' in result
        assert 'transactions' in result
        assert 'currency' in result

    def test_multiple_skills_sorted_by_name(self):
        from marcel_core.skills.loader import SkillDoc, format_components_catalog

        comp_a = ComponentSchema(name='widget_a', description='A', skill='zeta', props={})
        comp_b = ComponentSchema(name='widget_b', description='B', skill='alpha', props={})

        skill_zeta = SkillDoc(
            name='zeta', description='', content='', is_setup=False, source='data', components=[comp_a]
        )
        skill_alpha = SkillDoc(
            name='alpha', description='', content='', is_setup=False, source='data', components=[comp_b]
        )

        # Deliberately wrong order — format_components_catalog should sort.
        result = format_components_catalog([skill_zeta, skill_alpha])
        lines = result.split('\n')
        assert lines[0].startswith('- **widget_b**')  # alpha sorts first
        assert lines[1].startswith('- **widget_a**')

    def test_props_without_object_type_shows_no_props(self):
        from marcel_core.skills.loader import SkillDoc, format_components_catalog

        component = ComponentSchema(name='plain', description='x', skill='s', props={})
        skill = SkillDoc(name='s', description='', content='', is_setup=False, source='data', components=[component])
        result = format_components_catalog([skill])
        assert '(no props)' in result


class TestChannelRichUI:
    def test_telegram_is_rich(self):
        from marcel_core.channels.adapter import channel_supports_rich_ui

        assert channel_supports_rich_ui('telegram') is True

    def test_websocket_is_rich(self):
        from marcel_core.channels.adapter import channel_supports_rich_ui

        assert channel_supports_rich_ui('websocket') is True

    def test_cli_is_not_rich(self):
        from marcel_core.channels.adapter import channel_supports_rich_ui

        assert channel_supports_rich_ui('cli') is False

    def test_job_is_not_rich(self):
        from marcel_core.channels.adapter import channel_supports_rich_ui

        assert channel_supports_rich_ui('job') is False

    def test_unknown_channel_is_not_rich(self):
        from marcel_core.channels.adapter import channel_supports_rich_ui

        assert channel_supports_rich_ui('carrier-pigeon') is False


class TestSkillLoaderComponents:
    def test_loads_components_yaml(self, tmp_path):
        from marcel_core.skills.loader import _load_skill_dir

        skill_dir = tmp_path / 'test-skill'
        skill_dir.mkdir()
        (skill_dir / 'SKILL.md').write_text('---\nname: test\ndescription: Test\n---\n\nBody.')
        (skill_dir / 'components.yaml').write_text(
            'components:\n  - name: my_widget\n    description: A widget\n    props:\n      type: object\n'
        )
        doc = _load_skill_dir(skill_dir, 'user', 'project')
        assert doc is not None
        assert len(doc.components) == 1
        assert doc.components[0].name == 'my_widget'
        assert doc.components[0].skill == 'test'  # Uses resolved name from frontmatter

    def test_no_components_yaml(self, tmp_path):
        from marcel_core.skills.loader import _load_skill_dir

        skill_dir = tmp_path / 'test-skill'
        skill_dir.mkdir()
        (skill_dir / 'SKILL.md').write_text('---\nname: test\n---\n\nBody.')
        doc = _load_skill_dir(skill_dir, 'user', 'project')
        assert doc is not None
        assert doc.components == []

    def test_components_with_setup_fallback(self, tmp_path, monkeypatch):
        """Components are attached even when falling back to SETUP.md."""
        from marcel_core.skills.loader import _load_skill_dir

        monkeypatch.delenv('MISSING_VAR', raising=False)
        skill_dir = tmp_path / 'test-skill'
        skill_dir.mkdir()
        (skill_dir / 'SKILL.md').write_text('---\nname: test\nrequires:\n  env:\n    - MISSING_VAR\n---\n\nFull skill.')
        (skill_dir / 'SETUP.md').write_text('---\nname: test\n---\n\nSetup guide.')
        (skill_dir / 'components.yaml').write_text('components:\n  - name: test_widget\n    props: {}\n')
        doc = _load_skill_dir(skill_dir, 'user', 'project')
        assert doc is not None
        assert doc.is_setup is True
        assert len(doc.components) == 1
        assert doc.components[0].name == 'test_widget'


# ---------------------------------------------------------------------------
# /api/components REST endpoints
# ---------------------------------------------------------------------------


class _LinkedTelegramChannel:
    def resolve_user_slug(self, external_id: str) -> str | None:
        return 'alice' if external_id == '42' else None


class TestComponentsAPI:
    @pytest.fixture(autouse=True)
    def _auth_and_catalog(self, monkeypatch):
        """Fake Telegram auth + a one-component catalog for the endpoints."""
        monkeypatch.setattr(
            'marcel_core.api.components.verify_telegram_init_data',
            lambda init: {'id': 42} if init == 'valid-init' else None,
        )
        monkeypatch.setattr('marcel_core.api.components.get_channel', lambda name: _LinkedTelegramChannel())
        registry = ComponentRegistry(
            [
                ComponentSchema(
                    name='transaction_list',
                    description='List of bank transactions',
                    skill='banking',
                    props={'type': 'object', 'properties': {'transactions': {'type': 'array'}}},
                )
            ]
        )
        monkeypatch.setattr('marcel_core.api.components.build_registry', lambda user_slug: registry)

    def _client(self):
        from marcel_core.main import app

        return TestClient(app)

    def test_list_components_with_telegram_auth(self):
        resp = self._client().get('/api/components', params={'initData': 'valid-init'})
        assert resp.status_code == 200
        components = resp.json()['components']
        assert [c['name'] for c in components] == ['transaction_list']
        assert components[0]['skill'] == 'banking'
        assert components[0]['props']['type'] == 'object'

    def test_get_component_by_name(self):
        resp = self._client().get('/api/components/transaction_list', params={'initData': 'valid-init'})
        assert resp.status_code == 200
        body = resp.json()
        assert body['name'] == 'transaction_list'
        assert body['description'] == 'List of bank transactions'

    def test_get_unknown_component_returns_404(self):
        resp = self._client().get('/api/components/flux_capacitor', params={'initData': 'valid-init'})
        assert resp.status_code == 404
        assert 'flux_capacitor' in resp.json()['detail']

    def test_invalid_init_data_returns_401(self):
        resp = self._client().get('/api/components', params={'initData': 'tampered-blob'})
        assert resp.status_code == 401
        assert 'telegram' in resp.json()['detail'].lower()

    def test_unlinked_telegram_user_returns_401(self, monkeypatch):
        monkeypatch.setattr('marcel_core.api.components.get_channel', lambda name: None)
        resp = self._client().get('/api/components', params={'initData': 'valid-init'})
        assert resp.status_code == 401
        assert 'not linked' in resp.json()['detail'].lower()

    def test_bad_api_token_returns_401(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_api_token', 'real-secret')
        resp = self._client().get('/api/components', headers={'authorization': 'Bearer wrong-token'})
        assert resp.status_code == 401

    def test_valid_api_token_still_requires_init_data(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_api_token', 'real-secret')
        resp = self._client().get('/api/components', headers={'authorization': 'Bearer real-secret'})
        assert resp.status_code == 400
        assert 'initData' in resp.json()['detail']

    def test_get_component_auth_failure_returns_401(self):
        resp = self._client().get('/api/components/transaction_list', params={'initData': 'tampered'})
        assert resp.status_code == 401
