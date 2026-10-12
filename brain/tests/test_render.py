"""The template renders with the deploy variables: every placeholder filled, links in place."""

import json
from pathlib import Path

import pytest
from conftest import BRAIN

import render
from render import VARIABLES, RenderError


def test_renders_with_every_variable_and_leaves_no_placeholder(
    tmp_path: Path, values: dict[str, str]
) -> None:
    out = tmp_path / 'brain'
    written = render.render(out, values)

    assert {p.relative_to(out).as_posix() for p in written} == {
        'CLAUDE.md',
        '.claude/settings.json',
        'MEMORY.md',
        'memory',
    }
    persona = (out / 'CLAUDE.md').read_text()
    settings = (out / '.claude' / 'settings.json').read_text()
    for text in (persona, settings):
        assert '{{' not in text and '}}' not in text
    assert persona.startswith('# You are Marcel\n'), 'the template note is dropped'
    assert 'Shaun' in persona
    source = json.loads(settings)['extraKnownMarketplaces']['marcel-local']['source']
    assert source == {'source': 'directory', 'path': values['MARKETPLACE_DIR']}


def test_links_memory_to_the_hubs_memory_folder(tmp_path: Path, values: dict[str, str]) -> None:
    memory = Path(values['MEMORY_DIR'])
    (memory / 'memory').mkdir(parents=True)
    (memory / 'MEMORY.md').write_text('# Memory\n')
    out = tmp_path / 'brain'

    render.render(out, values)
    render.render(out, values)  # a second render replaces its own links

    assert (out / 'MEMORY.md').is_symlink()
    assert (out / 'MEMORY.md').read_text() == '# Memory\n'
    assert (out / 'memory').resolve() == (memory / 'memory').resolve()


def test_first_deploy_creates_the_memory_targets(tmp_path: Path, values: dict[str, str]) -> None:
    out = tmp_path / 'brain'
    render.render(out, values)  # MEMORY_DIR does not exist yet
    assert (out / 'MEMORY.md').read_text() == ''
    assert (out / 'memory').is_dir()


def test_an_existing_memory_is_never_overwritten(tmp_path: Path, values: dict[str, str]) -> None:
    memory = Path(values['MEMORY_DIR'])
    memory.mkdir(parents=True)
    (memory / 'MEMORY.md').write_text('# Kept\n')
    render.render(tmp_path / 'brain', values)
    assert (memory / 'MEMORY.md').read_text() == '# Kept\n'


def test_never_replaces_a_real_file_with_a_link(tmp_path: Path, values: dict[str, str]) -> None:
    out = tmp_path / 'brain'
    out.mkdir()
    (out / 'MEMORY.md').write_text('notes nobody saved elsewhere')
    with pytest.raises(RenderError, match='real file'):
        render.render(out, values)
    assert (out / 'MEMORY.md').read_text() == 'notes nobody saved elsewhere'


def test_json_values_are_escaped(tmp_path: Path, values: dict[str, str]) -> None:
    values['MARKETPLACE_DIR'] = str(tmp_path / 'a "quoted" \\ dir')
    render.render(tmp_path / 'brain', values)
    settings = json.loads((tmp_path / 'brain' / '.claude' / 'settings.json').read_text())
    assert settings['extraKnownMarketplaces']['marcel-local']['source']['path'] == values['MARKETPLACE_DIR']


def test_agent_name_defaults_to_marcel_and_can_change(tmp_path: Path, values: dict[str, str]) -> None:
    assert render.resolve(values)['AGENT_NAME'] == 'Marcel'
    values['AGENT_NAME'] = 'Gigi'
    render.render(tmp_path / 'brain', values)
    assert (tmp_path / 'brain' / 'CLAUDE.md').read_text().startswith('# You are Gigi\n')


@pytest.mark.parametrize(
    ('change', 'message'),
    [
        ({'NAME_TYPO': 'x'}, 'Unknown variable NAME_TYPO'),
        ({'OWNER_NAME': ''}, 'OWNER_NAME is not set'),
        ({'OWNER_NAME': 'two\nlines'}, 'must be one line'),
        ({'MEMORY_DIR': 'data/memory'}, 'must be an absolute path'),
    ],
)
def test_refuses_bad_values(values: dict[str, str], change: dict[str, str], message: str) -> None:
    with pytest.raises(RenderError, match=message):
        render.resolve(values | change)


def test_refuses_a_missing_variable(values: dict[str, str]) -> None:
    del values['MEMORY_DIR']
    with pytest.raises(RenderError, match='MEMORY_DIR is not set'):
        render.resolve(values)


def test_refuses_a_placeholder_with_no_variable(tmp_path: Path, values: dict[str, str]) -> None:
    (tmp_path / 'CLAUDE.md').write_text('Hello {{NOT_A_VARIABLE}}')
    with pytest.raises(RenderError, match='NOT_A_VARIABLE'):
        render.render_text('CLAUDE.md', render.resolve(values), tmp_path)


def test_refuses_a_broken_placeholder(values: dict[str, str]) -> None:
    with pytest.raises(RenderError, match='left in the output'):
        render.fill('Hello {{OWNER NAME}}', render.resolve(values))


def test_cli_renders_and_reports(
    tmp_path: Path, values: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / 'brain'
    args = ['--out', str(out)] + [arg for k, v in values.items() for arg in ('--var', f'{k}={v}')]
    assert render.main(args) == 0
    assert 'Brain workspace rendered' in capsys.readouterr().out
    assert (out / 'CLAUDE.md').is_file()


def test_cli_says_why_it_refused(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert render.main(['--out', str(tmp_path), '--var', 'OWNER_NAME']) == 1
    assert 'not NAME=value' in capsys.readouterr().err
    assert render.main(['--out', str(tmp_path), '--var', 'OWNER_NAME=Shaun']) == 1
    assert 'MARKETPLACE_DIR is not set' in capsys.readouterr().err


def test_every_variable_is_used_and_documented() -> None:
    template = ''.join((BRAIN / rel).read_text() for rel in render.TEMPLATE_FILES)
    readme = (BRAIN / 'README.md').read_text()
    used = set(render.PLACEHOLDER.findall(template))
    assert used <= set(VARIABLES), f'placeholders with no variable: {sorted(used - set(VARIABLES))}'
    for name in VARIABLES:
        assert name in used or name == 'MEMORY_DIR', f'{name} is defined but the template never uses it'
        assert f'`{name}`' in readme, f'brain/README.md does not document {name}'
