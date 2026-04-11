"""Scenario-based tests for storage/artifacts.py.

Covers: create, save, load, list, files_dir through a realistic
artifact creation and retrieval workflow.
"""

from __future__ import annotations

import pytest

from marcel_core.storage import _root
from marcel_core.storage.artifacts import (
    Artifact,
    create_artifact,
    files_dir,
    list_artifacts,
    load_artifact,
    save_artifact,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


class TestArtifactLifecycle:
    def test_create_and_load(self):
        artifact_id = create_artifact(
            user_slug='alice',
            conversation_id='conv-1',
            content_type='markdown',
            content='# Hello\nWorld',
            title='Test Artifact',
        )
        assert artifact_id

        loaded = load_artifact(artifact_id)
        assert loaded is not None
        assert loaded.user_slug == 'alice'
        assert loaded.content == '# Hello\nWorld'
        assert loaded.title == 'Test Artifact'
        assert loaded.content_type == 'markdown'

    def test_load_nonexistent(self):
        assert load_artifact('nonexistent') is None

    def test_save_and_overwrite(self):
        artifact = Artifact(
            user_slug='alice',
            conversation_id='conv-1',
            content_type='checklist',
            content='- [ ] Item 1',
            title='Checklist',
        )
        save_artifact(artifact)

        artifact.content = '- [x] Item 1'
        save_artifact(artifact)

        loaded = load_artifact(artifact.id)
        assert loaded is not None
        assert '- [x]' in loaded.content

    def test_list_artifacts_filters_by_user(self):
        create_artifact('alice', 'conv-1', 'markdown', 'a', 'Alice Art')
        create_artifact('bob', 'conv-2', 'markdown', 'b', 'Bob Art')

        alice_arts = list_artifacts('alice')
        assert len(alice_arts) == 1
        assert alice_arts[0].title == 'Alice Art'

    def test_list_artifacts_filters_by_conversation(self):
        create_artifact('alice', 'conv-1', 'markdown', 'a', 'Conv 1')
        create_artifact('alice', 'conv-2', 'markdown', 'b', 'Conv 2')

        results = list_artifacts('alice', conversation_id='conv-1')
        assert len(results) == 1
        assert results[0].title == 'Conv 1'

    def test_list_artifacts_limit(self):
        for i in range(5):
            create_artifact('alice', 'conv-1', 'markdown', f'content-{i}', f'Art {i}')

        results = list_artifacts('alice', limit=3)
        assert len(results) == 3

    def test_list_artifacts_empty(self):
        assert list_artifacts('alice') == []

    def test_files_dir_creates_directory(self, tmp_path):
        d = files_dir()
        assert d.exists()
        assert d.is_dir()

    def test_corrupt_artifact_returns_none(self, tmp_path):
        arts_dir = tmp_path / 'artifacts'
        arts_dir.mkdir()
        (arts_dir / 'bad.json').write_text('not valid json')
        assert load_artifact('bad') is None

    def test_list_ignores_corrupt(self, tmp_path):
        create_artifact('alice', 'c', 'markdown', 'good', 'Good')
        # Write a corrupt file
        arts_dir = tmp_path / 'artifacts'
        (arts_dir / 'corrupt.json').write_text('not json')

        results = list_artifacts('alice')
        assert len(results) == 1
