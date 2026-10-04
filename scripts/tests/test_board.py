from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import board  # noqa: E402


def write_story(directory: Path, sid: str, status: str, touches: str, depends: str = '') -> None:
    (directory / f'{sid}.md').write_text(
        f'---\nid: {sid}\ntitle: {sid} title\nfeature: F99\nwave: 1\nruns_on: cloud\n'
        f'size: S\nstatus: {status}\ndepends: {depends}\ntouches: {touches}\n---\n\n# {sid}\n'
    )


@pytest.fixture
def stories_dir(tmp_path: Path) -> Path:
    write_story(tmp_path, 'S-01.1', 'Done', 'README.md')
    write_story(tmp_path, 'S-02.1', 'In Progress', 'hub/src/marcel_hub/tasks/ hub/tests/')
    return tmp_path


def test_start_refuses_overlapping_touches(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    write_story(stories_dir, 'S-02.2', 'Backlog', 'hub/src/marcel_hub/{tasks,ws}/')
    assert board.main(['start', 'S-02.2'], stories_dir) == 1
    err = capsys.readouterr().err
    assert 'overlaps S-02.1' in err
    assert board.load(stories_dir)['S-02.2'].status == 'Backlog'


def test_start_allows_disjoint_touches(stories_dir: Path):
    write_story(stories_dir, 'S-03.1', 'Backlog', 'runner/src/', depends='S-01.1')
    assert board.main(['start', 'S-03.1'], stories_dir) == 0
    assert board.load(stories_dir)['S-03.1'].status == 'In Progress'


def test_start_refuses_unfinished_dependency(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    write_story(stories_dir, 'S-03.2', 'Backlog', 'runner/src/x.py', depends='S-02.1')
    assert board.main(['start', 'S-03.2'], stories_dir) == 1
    assert 'depends on S-02.1, which is In Progress' in capsys.readouterr().err


def test_start_refuses_unknown_dependency(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    write_story(stories_dir, 'S-03.3', 'Backlog', 'runner/src/y.py', depends='SP9')
    assert board.main(['start', 'S-03.3'], stories_dir) == 1
    assert 'SP9, which is not on the board' in capsys.readouterr().err


def test_wip_cap_counts_stories_not_spikes(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    for n in range(board.WIP_CAP - 1):
        write_story(stories_dir, f'S-09.{n}', 'In Progress', f'lane{n}/')
    write_story(stories_dir, 'SP1', 'In Progress', 'scratch/spikes/SP1-*')
    write_story(stories_dir, 'S-10.1', 'Backlog', 'other/')
    assert board.main(['start', 'S-10.1'], stories_dir) == 1
    assert f'cap {board.WIP_CAP}' in capsys.readouterr().err


def test_everything_touch_overlaps_any_path():
    assert board.overlaps(['everything', '(one-off)'], ['ios/'])


def test_glob_prefixes_compare_literally():
    assert not board.overlaps(['scratch/spikes/SP1-*'], ['scratch/spikes/SP2-*'])
    assert board.overlaps(['hub/'], ['hub/src/marcel_hub/app.py'])


def test_set_rejects_unknown_status(stories_dir: Path):
    with pytest.raises(SystemExit, match='Status must be one of'):
        board.main(['set', 'S-01.1', 'status', 'Shipped'], stories_dir)


def test_set_and_list(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    assert board.main(['set', 'S-02.1', 'status', 'Done'], stories_dir) == 0
    assert board.main(['list', '--status', 'Done'], stories_dir) == 0
    out = capsys.readouterr().out
    assert 'S-02.1' in out and 'S-01.1' in out


def test_real_board_dependencies_all_resolve():
    stories = board.load()
    missing = [(s.id, d) for s in stories.values() for d in s.depends if d not in stories]
    assert stories, 'project/stories is empty'
    assert missing == []


def test_spikes_do_not_count_toward_the_cap(stories_dir: Path):
    # stories_dir already has one story In Progress (S-02.1); fill up to one below the cap.
    for n in range(board.WIP_CAP - 2):
        write_story(stories_dir, f'S-09.{n}', 'In Progress', f'lane{n}/')
    for n in range(1, 8):
        write_story(stories_dir, f'SP{n}', 'In Progress', f'scratch/spikes/SP{n}-*')
    write_story(stories_dir, 'S-10.1', 'Backlog', 'other/')
    assert board.main(['start', 'S-10.1'], stories_dir) == 0


def test_story_without_frontmatter_is_refused(stories_dir: Path):
    (stories_dir / 'broken.md').write_text('# no frontmatter here\n')
    with pytest.raises(board.BoardError, match='broken.md has no frontmatter id'):
        board.load(stories_dir)


def test_misspelled_status_is_refused(stories_dir: Path):
    write_story(stories_dir, 'S-07.7', 'In progress', 'x/')
    with pytest.raises(board.BoardError, match='status "In progress"'):
        board.load(stories_dir)


def test_duplicate_id_is_refused(stories_dir: Path):
    write_story(stories_dir, 'S-07.8', 'Backlog', 'x/')
    (stories_dir / 'copy.md').write_text((stories_dir / 'S-07.8.md').read_text())
    with pytest.raises(board.BoardError, match='appears twice'):
        board.load(stories_dir)


def test_real_hub_stories_can_run_in_parallel():
    """Stories in one lane collide only when their source files do, not via shared tests/."""
    stories = board.load()
    assert not board.overlaps(stories['S-04.4'].touches, stories['S-04.7'].touches)
    assert not board.overlaps(stories['S-03.2'].touches, stories['S-03.4'].touches)
    assert board.overlaps(stories['S-08.1'].touches, stories['S-08.2'].touches)


def test_list_filters_by_wave_and_place(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    write_story(stories_dir, 'S-12.1', 'Backlog', 'ios/')
    board.set_field(board.load(stories_dir)['S-12.1'], 'runs_on', 'Mac')
    board.set_field(board.load(stories_dir)['S-12.1'], 'wave', '2')
    assert board.main(['list', '--wave', '2', '--runs-on', 'Mac'], stories_dir) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert [line.split()[0] for line in out] == ['S-12.1']


def test_lanes_shows_in_progress_and_ready(stories_dir: Path, capsys: pytest.CaptureFixture[str]):
    write_story(stories_dir, 'S-03.1', 'Backlog', 'runner/', depends='S-01.1')
    write_story(stories_dir, 'S-03.2', 'Backlog', 'runner/x.py', depends='S-02.1')
    assert board.main(['lanes'], stories_dir) == 0
    out = capsys.readouterr().out
    in_progress, _, ready = out.partition('Ready to start:')
    assert 'S-02.1' in in_progress and 'touches: hub/src/marcel_hub/tasks/' in in_progress
    assert 'S-03.1' in ready and 'S-03.2' not in ready


def test_start_refuses_a_story_already_started(
    stories_dir: Path, capsys: pytest.CaptureFixture[str]
):
    assert board.main(['start', 'S-02.1'], stories_dir) == 1
    assert 'already In Progress' in capsys.readouterr().err


@pytest.mark.parametrize('command', [['start', 'S-99.9'], ['set', 'S-99.9', 'status', 'Done']])
def test_unknown_story_is_reported(
    stories_dir: Path, capsys: pytest.CaptureFixture[str], command: list[str]
):
    assert board.main(command, stories_dir) == 1
    assert 'No story S-99.9' in capsys.readouterr().err


def test_set_adds_a_missing_key(stories_dir: Path):
    story = board.load(stories_dir)['S-01.1']
    board.set_field(story, 'owner', 'lead')
    assert board.load(stories_dir)['S-01.1'].fields['owner'] == 'lead'


def test_text_without_frontmatter_has_no_fields():
    assert board.read_frontmatter('# just a heading\n') == {}
