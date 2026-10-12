from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import board  # noqa: E402


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    stories, export = tmp_path / 'stories', tmp_path / 'export'
    stories.mkdir()
    export.mkdir()
    return stories, export


def write_story(
    dirs: tuple[Path, Path],
    sid: str,
    status: str | None,
    touches: str,
    depends: str = '',
    **board_fields: str,
) -> None:
    """A story file (definition) plus, unless status is None, its row in the board export."""
    stories, export = dirs
    (stories / f'{sid}.md').write_text(
        f'---\nid: {sid}\ntitle: {sid} title\nfeature: F99\nwave: 1\nruns_on: cloud\n'
        f'size: S\ndepends: {depends}\ntouches: {touches}\n---\n\n# {sid}\n'
    )
    if status is not None:
        row = {'id': sid, 'status': status, 'touches': touches} | board_fields
        (export / f'{sid}.json').write_text(json.dumps(row))


def run(dirs: tuple[Path, Path], *argv: str) -> int:
    return board.main(list(argv), *dirs)


@pytest.fixture
def board_dirs(dirs: tuple[Path, Path]) -> tuple[Path, Path]:
    write_story(dirs, 'S-01.1', 'Done', 'README.md')
    write_story(dirs, 'S-02.1', 'In Progress', 'hub/src/marcel_hub/tasks/ hub/tests/', owner='mac')
    return dirs


def test_start_refuses_overlapping_touches(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-02.2', 'Backlog', 'hub/src/marcel_hub/{tasks,ws}/')
    assert run(board_dirs, 'start', 'S-02.2') == 1
    assert 'overlaps S-02.1' in capsys.readouterr().err


def test_a_story_in_review_still_holds_its_files(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-05.1', 'Review', 'plugins/marcel/channel/')
    write_story(board_dirs, 'S-05.2', 'Backlog', 'plugins/marcel/channel/src/x.ts')
    assert run(board_dirs, 'start', 'S-05.2') == 1
    assert 'overlaps S-05.1' in capsys.readouterr().err


def test_start_allows_disjoint_touches_and_prints_the_claim(
    board_dirs, capsys: pytest.CaptureFixture[str]
):
    write_story(board_dirs, 'S-03.1', 'Backlog', 'runner/src/', depends='S-01.1')
    assert run(board_dirs, 'start', 'S-03.1') == 0
    out = capsys.readouterr().out
    assert 'S-03.1 can start' in out and '"branch": "story/S-03.1"' in out
    # The CLI never writes status: the export is unchanged.
    assert board.load(*board_dirs)['S-03.1'].status == 'Backlog'


def test_start_refuses_unfinished_dependency(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-03.2', 'Backlog', 'runner/src/x.py', depends='S-02.1')
    assert run(board_dirs, 'start', 'S-03.2') == 1
    assert 'depends on S-02.1, which is In Progress' in capsys.readouterr().err


def test_start_refuses_unknown_dependency(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-03.3', 'Backlog', 'runner/src/y.py', depends='SP9')
    assert run(board_dirs, 'start', 'S-03.3') == 1
    assert 'SP9, which is not on the board' in capsys.readouterr().err


def test_start_refuses_a_story_missing_from_the_board(
    board_dirs, capsys: pytest.CaptureFixture[str]
):
    write_story(board_dirs, 'S-03.4', None, 'runner/src/z.py')
    assert run(board_dirs, 'start', 'S-03.4') == 1
    assert 'not on the build board yet' in capsys.readouterr().err


def test_wip_cap_counts_stories_not_spikes(board_dirs, capsys: pytest.CaptureFixture[str]):
    for n in range(board.WIP_CAP - 1):
        write_story(board_dirs, f'S-09.{n}', 'In Progress', f'lane{n}/')
    write_story(board_dirs, 'SP1', 'In Progress', 'scratch/spikes/SP1-*')
    write_story(board_dirs, 'S-10.1', 'Backlog', 'other/')
    assert run(board_dirs, 'start', 'S-10.1') == 1
    assert f'cap {board.WIP_CAP}' in capsys.readouterr().err


def test_spikes_do_not_count_toward_the_cap(board_dirs):
    for n in range(board.WIP_CAP - 2):
        write_story(board_dirs, f'S-09.{n}', 'In Progress', f'lane{n}/')
    for n in range(1, 8):
        write_story(board_dirs, f'SP{n}', 'In Progress', f'scratch/spikes/SP{n}-*')
    write_story(board_dirs, 'S-10.1', 'Backlog', 'other/')
    assert run(board_dirs, 'start', 'S-10.1') == 0


def test_everything_touch_overlaps_any_path():
    assert board.overlaps(['everything', '(one-off)'], ['ios/'])


def test_glob_prefixes_compare_literally():
    assert not board.overlaps(['scratch/spikes/SP1-*'], ['scratch/spikes/SP2-*'])
    assert board.overlaps(['hub/'], ['hub/src/marcel_hub/app.py'])


def test_missing_export_says_how_to_make_one(dirs):
    stories, _ = dirs
    with pytest.raises(board.BoardError, match='Export the board first'):
        board.main(['lanes'], stories, stories.parent / 'nowhere')


def test_unknown_status_on_the_board_is_refused(dirs):
    write_story(dirs, 'S-07.7', 'In progress', 'x/')
    with pytest.raises(board.BoardError, match='"In progress"'):
        board.load(*dirs)


def test_status_line_in_a_story_file_is_refused(dirs):
    stories, _ = dirs
    (stories / 'S-07.9.md').write_text('---\nid: S-07.9\nstatus: Done\n---\n')
    with pytest.raises(board.BoardError, match='status lives on the build board'):
        board.load(*dirs)


def test_story_without_frontmatter_is_refused(board_dirs):
    (board_dirs[0] / 'broken.md').write_text('# no frontmatter here\n')
    with pytest.raises(board.BoardError, match='broken.md has no frontmatter id'):
        board.load(*board_dirs)


def test_duplicate_id_is_refused(board_dirs):
    write_story(board_dirs, 'S-07.8', 'Backlog', 'x/')
    (board_dirs[0] / 'copy.md').write_text((board_dirs[0] / 'S-07.8.md').read_text())
    with pytest.raises(board.BoardError, match='appears twice'):
        board.load(*board_dirs)


def test_list_filters(board_dirs, capsys: pytest.CaptureFixture[str]):
    assert run(board_dirs, 'list', '--status', 'Done', '--wave', '1', '--runs-on', 'cloud') == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ['S-01.1']
    assert run(board_dirs, 'list', '--wave', '7') == 0
    assert run(board_dirs, 'list', '--runs-on', 'Mac') == 0
    assert capsys.readouterr().out == ''


def test_ready_filters_by_place(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-03.1', 'Backlog', 'runner/', depends='S-01.1')
    assert run(board_dirs, 'ready', '--runs-on', 'Mac') == 0
    assert capsys.readouterr().out == ''
    assert run(board_dirs, 'ready', '--runs-on', 'cloud') == 0
    assert 'S-03.1' in capsys.readouterr().out


def test_lanes_shows_in_flight_blocked_and_ready(board_dirs, capsys: pytest.CaptureFixture[str]):
    write_story(board_dirs, 'S-03.1', 'Backlog', 'runner/', depends='S-01.1')
    write_story(board_dirs, 'S-03.2', 'Backlog', 'runner/x.py', depends='S-02.1')
    write_story(board_dirs, 'S-04.2', 'Blocked', 'hub/auth/', blocked_reason='contract request')
    assert run(board_dirs, 'lanes') == 0
    out = capsys.readouterr().out
    in_flight, _, ready = out.partition('Ready to start:')
    assert 'board export from' in out
    assert 'S-02.1' in in_flight and 'owner: mac' in in_flight
    assert 'why: contract request' in in_flight
    assert 'S-03.1' in ready and 'S-03.2' not in ready


def test_start_refuses_a_story_already_started(board_dirs, capsys: pytest.CaptureFixture[str]):
    assert run(board_dirs, 'start', 'S-02.1') == 1
    assert 'already In Progress' in capsys.readouterr().err


def test_unknown_story_is_reported(board_dirs, capsys: pytest.CaptureFixture[str]):
    assert run(board_dirs, 'start', 'S-99.9') == 1
    assert 'No story S-99.9' in capsys.readouterr().err


def test_sync_creates_new_rows_and_never_sends_status_for_existing_ones(
    board_dirs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    write_story(board_dirs, 'S-03.1', None, 'runner/')
    (board_dirs[1] / 'S-00.0.json').write_text(json.dumps({'status': 'Done'}))
    out = tmp_path / 'sync.json'
    assert run(board_dirs, 'sync', str(out)) == 0
    writes = {w['doc_id']: w for w in json.loads(out.read_text())}
    assert writes['S-03.1']['op'] == 'set' and writes['S-03.1']['data']['status'] == 'Backlog'
    update = writes['S-02.1']
    assert update['op'] == 'update' and update['needs_if_version']
    assert 'status' not in update['data'] and update['data']['title'] == 'S-02.1 title'
    assert 'S-00.0' in capsys.readouterr().out


def test_sync_is_empty_when_the_board_matches(board_dirs, tmp_path: Path):
    titles = board.feature_titles()
    for story in board.load(*board_dirs).values():
        path = board_dirs[1] / f'{story.id}.json'
        path.write_text(json.dumps(board.definition_row(story, titles) | {'status': 'Done'}))
    out = tmp_path / 'sync.json'
    assert run(board_dirs, 'sync', str(out)) == 0
    assert json.loads(out.read_text()) == []


def test_rows_writes_one_definition_per_story(board_dirs, tmp_path: Path):
    assert run(board_dirs, 'rows', str(tmp_path / 'rows')) == 0
    row = json.loads((tmp_path / 'rows' / 'S-02.1.json').read_text())
    assert row['depends'] == [] and 'status' not in row


def test_export_age_of_an_empty_export(tmp_path: Path):
    assert board.export_age(tmp_path) == 'the board export is empty'


def test_text_without_frontmatter_has_no_fields():
    assert board.read_frontmatter('# just a heading\n') == {}


def test_real_board_dependencies_all_resolve():
    stories = board.load_definitions()
    missing = [(s.id, d) for s in stories.values() for d in s.depends if d not in stories]
    assert stories, 'project/stories is empty'
    assert missing == []


def test_real_hub_stories_can_run_in_parallel():
    """Stories in one lane collide only when their source files do, not via shared tests/."""
    stories = board.load_definitions()
    assert not board.overlaps(stories['S-04.4'].touches, stories['S-04.7'].touches)
    assert not board.overlaps(stories['S-03.2'].touches, stories['S-03.4'].touches)
    assert board.overlaps(stories['S-08.1'].touches, stories['S-08.2'].touches)
