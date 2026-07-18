"""Tests for the skill → deferred capability factory (FEAT-260718-85b545).

Each visible skill becomes a pydantic-ai ``Capability``: the catalog shows its
id + description, ``load_capability`` returns the SKILL.md body as instructions,
and a skill-root-scoped ``read_skill_resource`` tool (owned by the capability)
serves resource files. A ``/<skill>`` override is built eager.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marcel_core.skills.capability import _skill_capability, build_skill_capabilities
from marcel_core.skills.loader import SkillDoc


def _doc(name='news', description='News digest', content='How to read news.', *, is_setup=False, skill_dir=None):
    return SkillDoc(
        name=name,
        description=description,
        content=content,
        source='zoo-global',
        skill_dir=skill_dir or Path('/x'),
        is_setup=is_setup,
    )


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Write a real zoo skill and point settings at it."""
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    (zoo / 'skills').mkdir(parents=True)
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path / 'data'))

    def make(name, *, skill_md, files=None):
        base = zoo / 'skills' / name
        base.mkdir(parents=True)
        (base / 'SKILL.md').write_text(skill_md)
        for fname, content in (files or {}).items():
            (base / fname).write_text(content)
        return base

    return make


class TestSkillCapabilityShape:
    def test_id_and_instructions_and_description(self):
        cap = _skill_capability(_doc(), eager=False)
        assert cap.id == 'news'
        assert cap.description == 'News digest'
        assert cap.get_instructions() == ['How to read news.']

    def test_defer_loading_toggles_with_eager(self):
        assert _skill_capability(_doc(), eager=False).defer_loading is True
        assert _skill_capability(_doc(), eager=True).defer_loading is False

    def test_setup_skill_description_marked(self):
        cap = _skill_capability(_doc(is_setup=True), eager=False)
        assert (cap.description or '').endswith('— needs setup')

    def test_exposes_a_single_resource_tool(self):
        cap = _skill_capability(_doc(), eager=False)
        assert len(cap.tools) == 1


class TestResourceTool:
    async def _call_resource_tool(self, cap, user_slug, resource):
        tool = cap.tools[0]
        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.user_slug = user_slug
        return await tool(ctx, resource)

    @pytest.mark.asyncio
    async def test_reads_resource_scoped_to_skill(self, roots):
        roots('news', skill_md='---\nname: news\ndescription: News\n---\n\nBody.', files={'feeds.yaml': 'feeds: []'})
        cap = build_skill_capabilities('shaun')[0]
        result = await self._call_resource_tool(cap, 'shaun', 'feeds')
        assert result == 'feeds: []'

    @pytest.mark.asyncio
    async def test_missing_resource_lists_available(self, roots):
        roots('news', skill_md='---\nname: news\ndescription: News\n---\n\nBody.', files={'feeds.yaml': 'x'})
        cap = build_skill_capabilities('shaun')[0]
        result = await self._call_resource_tool(cap, 'shaun', 'bogus')
        assert 'No resource' in result
        assert 'feeds.yaml' in result

    @pytest.mark.asyncio
    async def test_missing_resource_no_files_no_hint(self, roots):
        roots('news', skill_md='---\nname: news\ndescription: News\n---\n\nBody.')
        cap = build_skill_capabilities('shaun')[0]
        result = await self._call_resource_tool(cap, 'shaun', 'anything')
        assert 'No resource' in result
        assert 'Available:' not in result


class TestBuildSkillCapabilities:
    def test_one_capability_per_skill(self, roots):
        roots('alpha', skill_md='---\nname: alpha\ndescription: A\n---\n\nA.')
        roots('beta', skill_md='---\nname: beta\ndescription: B\n---\n\nB.')
        caps = build_skill_capabilities('shaun')
        assert sorted(str(c.id) for c in caps) == ['alpha', 'beta']
        assert all(c.defer_loading for c in caps)

    def test_eager_skill_is_not_deferred(self, roots):
        roots('alpha', skill_md='---\nname: alpha\ndescription: A\n---\n\nA.')
        roots('beta', skill_md='---\nname: beta\ndescription: B\n---\n\nB.')
        caps = {c.id: c for c in build_skill_capabilities('shaun', eager_skill='beta')}
        assert caps['beta'].defer_loading is False
        assert caps['alpha'].defer_loading is True

    def test_role_gating_applies(self, roots):
        roots('news', skill_md='---\nname: news\ndescription: News\n---\n\nN.')
        roots(
            'devtool',
            skill_md='---\nname: devtool\ndescription: Dev\nmetadata:\n  marcel-role: admin\n---\n\nD.',
        )
        user_caps = {c.id for c in build_skill_capabilities('shaun', role='user')}
        admin_caps = {c.id for c in build_skill_capabilities('shaun', role='admin')}
        assert 'devtool' not in user_caps
        assert 'devtool' in admin_caps
