"""Memory per buddy (S-17.4, B-20; one person since S-22.11, ADR-013).

Each agent's memory lives under `agents/<folder>/` of the one memory repo, and every way in goes
through one agent: the app (`/api/memory`, the buddy the phone shows), the brain (`/brain/memory`,
its own agent), a worker's notes, and a project's notes in a worker's briefing. The owner's memory
from before 0.7 is moved under `agents/<owner's folder>/` once, with its git history.
"""

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session
from test_brain_api import Brain
from test_channel_fakes import OWNER, ChannelHub, channel_hub, session_id
from test_tasks_fakes import run

from marcel_hub.memory import InvalidMemoryPath, MemoryRepo, MemoryUnavailable
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
)


class Family:
    """The one person's two buddies: Marcel (the fake hub's agent) and Pip (slug `pip`), each
    with a brain session and a phone that shows it (Emma's phone shows Pip)."""

    def __init__(self, hub: ChannelHub) -> None:
        self.hub = hub
        with Session(hub.engine) as s:
            pip = Agent(owner_user_id=OWNER, name='Pip', animal='fox', palette='marcel', seed='Pip',
                        slug='pip')  # fmt: skip
            s.add(pip)
            s.commit()
            main = Conversation(agent_id=pip.id, kind=ConversationKind.main)
            main.brain_session_id = session_id()
            s.add(main)
            s.commit()
            pip.main_conversation_id = main.id
            s.add(pip)
            s.commit()
            self.pip, emmas_brain = pip.id, main.brain_session_id
        self.owner_brain = Brain(hub, hub.brain())
        self.emma_brain = Brain(hub, emmas_brain)
        self.owner_phone = {'Authorization': f'Bearer {hub.token}'}
        self.emma_phone = {'Authorization': f'Bearer {hub.phone(agent_id=self.pip)}'}
        self.root = hub.tmp_path / 'memory'

    def app_put(self, who: dict[str, str], path: str, content: str) -> int:
        url = f'/api/memory/files/{path}'
        return self.hub.client.put(url, json={'content': content}, headers=who).status_code

    def app_get(self, who: dict[str, str], path: str) -> tuple[int, str]:
        res = self.hub.client.get(f'/api/memory/files/{path}', headers=who)
        return res.status_code, res.json().get('content', '')

    def task(self, agent_id: str) -> str:
        with Session(self.hub.engine) as s:
            task = Task(
                agent_id=agent_id,
                title='Homework',
                location=TaskLocation.nuc,
                state=TaskState.working,
                model='sonnet',
                origin=TaskOrigin.marcel,
            )
            s.add(task)
            s.commit()
            return task.id


@pytest.fixture
def family(tmp_path: Path) -> Iterator[Family]:
    with channel_hub(tmp_path) as hub:
        yield Family(hub)


# --- the four ways in -------------------------------------------------------------------------


def test_each_phone_shows_the_memory_of_the_buddy_it_shows(family: Family) -> None:
    assert (
        family.app_put(family.owner_phone, 'MEMORY.md', 'Shaun: the alarm code is 1234.\n') == 200
    )
    assert family.app_put(family.emma_phone, 'MEMORY.md', 'Emma: likes horses.\n') == 200

    assert family.app_get(family.owner_phone, 'MEMORY.md')[1] == 'Shaun: the alarm code is 1234.\n'
    assert family.app_get(family.emma_phone, 'MEMORY.md')[1] == 'Emma: likes horses.\n'
    tree = family.hub.client.get('/api/memory', headers=family.emma_phone).json()['files']
    assert [f['path'] for f in tree] == ['MEMORY.md']
    assert (family.root / 'agents' / 'pip' / 'MEMORY.md').read_text() == 'Emma: likes horses.\n'


@pytest.mark.parametrize(
    'path', ['../MEMORY.md', 'agents/x/MEMORY.md', '../../agents/pip/MEMORY.md', '.git/config.md']
)
def test_no_path_reaches_anothers_folder(family: Family, path: str) -> None:
    owners = 'marcel'
    family.app_put(family.owner_phone, 'MEMORY.md', 'secret\n')
    status, content = family.app_get(family.emma_phone, path.replace('x', owners))
    assert status in (400, 404) and 'secret' not in content


def test_the_brain_reads_and_writes_only_its_own_agents_memory(family: Family) -> None:
    owner, emma = family.owner_brain, family.emma_brain
    assert (
        owner.put('/memory', {'path': 'MEMORY.md', 'content': 'Owner notes.\n'}).status_code == 200
    )
    assert emma.get('/memory', path='MEMORY.md').status_code == 404  # not the owner's file
    assert emma.put('/memory', {'path': 'MEMORY.md', 'content': 'Pip notes.\n'}).status_code == 200
    assert owner.get('/memory', path='MEMORY.md').json()['content'] == 'Owner notes.\n'
    assert [f['path'] for f in emma.get('/memory').json()['files']] == ['MEMORY.md']
    assert (family.root / 'agents' / 'pip' / 'MEMORY.md').read_text() == 'Pip notes.\n'


def test_a_workers_note_goes_to_its_own_agents_project_notes(family: Family) -> None:
    gateway = family.hub.gateway
    run(gateway._store_note(family.task(family.pip), 'Homework is due on Fridays.'))
    pips = (family.root / 'agents' / 'pip' / 'projects' / 'inbox.md').read_text()
    assert 'Homework is due on Fridays.' in pips
    owners = family.root / 'agents' / 'marcel' / 'projects' / 'inbox.md'
    assert not owners.exists() or 'Homework' not in owners.read_text()


def test_a_worker_starts_with_its_own_agents_project_notes(family: Family) -> None:
    owners = family.root / 'agents' / 'marcel' / 'projects'
    owners.mkdir(parents=True)
    (owners / 'inbox.md').write_text('The owner bank PIN is 9876.\n')  # same slug, other buddy
    pips = family.root / 'agents' / 'pip' / 'projects'
    pips.mkdir(parents=True)
    (pips / 'inbox.md').write_text('Pip knows the school timetable.\n')
    projects = family.hub.app.state.projects
    with Session(family.hub.engine) as s:
        task = s.get(Task, family.task(family.pip))
    assert task is not None
    briefing = projects.briefing(task)
    assert 'school timetable' in briefing and '9876' not in briefing


# --- the one-time move ------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    cmd = ['git', f'--git-dir={root.with_name("memory.git")}', f'--work-tree={root}', *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def test_the_owners_old_memory_moves_under_agents_with_its_history(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    before = MemoryRepo(root)  # a hub from before 0.7: no agents, everything at the top
    before.write('MEMORY.md', 'The kids eat at 18:00.\n', None, 'app')
    etag = before.read('MEMORY.md')['etag']
    before.write('MEMORY.md', 'The kids eat at 18:30.\n', etag, 'agent')
    before.write('memory/2026-10-04.md', 'Dentist on Thursday.\n', None, 'app')
    before.write('projects/house.md', 'Heat pump: Daikin.\n', None, 'app')

    after = MemoryRepo(root, owner_folder=lambda: 'marcel')
    assert after.for_agent('Marcel').read('MEMORY.md')['content'] == 'The kids eat at 18:30.\n'

    moved = root / 'agents' / 'marcel'
    assert (moved / 'memory' / '2026-10-04.md').is_file()
    assert (moved / 'projects' / 'house.md').is_file()
    history = git(root, 'log', '--follow', '--format=%s', '--', 'agents/marcel/MEMORY.md')
    assert history.splitlines()[-2:] == ['memory: MEMORY.md (agent)', 'memory: MEMORY.md (app)']
    assert git(root, 'status', '--porcelain', '--', 'agents').strip() == ''  # all committed
    # Links at the old places for brain workspaces rendered before; the hub never follows one.
    assert (root / 'MEMORY.md').is_symlink() and (root / 'MEMORY.md').read_text() == (
        'The kids eat at 18:30.\n'
    )
    with pytest.raises(InvalidMemoryPath):
        after.read('MEMORY.md')
    after.for_agent('Marcel').read('MEMORY.md')  # a second look moves nothing again
    assert len(git(root, 'log', '--format=%s').splitlines()) == 5


def test_the_move_waits_for_the_owners_agent_and_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    MemoryRepo(root).write('MEMORY.md', 'old\n', None, 'app')
    MemoryRepo(root, owner_folder=lambda: None).tree()  # no owner agent yet: nothing moves
    assert (root / 'MEMORY.md').is_file() and not (root / 'MEMORY.md').is_symlink()

    (root / 'agents' / 'marcel').mkdir(parents=True)
    (root / 'agents' / 'marcel' / 'MEMORY.md').write_text('new\n')
    MemoryRepo(root, owner_folder=lambda: 'marcel').tree()  # a clash: it leaves both alone
    assert (root / 'MEMORY.md').read_text() == 'old\n'
    assert (root / 'agents' / 'marcel' / 'MEMORY.md').read_text() == 'new\n'


def test_an_agent_without_a_slug_gets_one_for_good(family: Family) -> None:
    """An agent an older `agent create` made has no slug. Its memory folder is fixed the first
    time it is needed, as `marcel-hub setup` would name it, so it never splits in two."""
    memory = family.hub.app.state.memory
    assert memory.folder_of(family.pip) == 'pip'
    assert memory.owner_folder() == 'marcel'
    with Session(family.hub.engine) as s:
        owners = s.get(Agent, family.hub.agent_id)
        assert owners is not None and (owners.slug, owners.owner_user_id) == ('marcel', OWNER)
        late = Agent(owner_user_id=OWNER, name='Pip', animal='fox', palette='marcel', seed='Pip')
        s.add(late)
        s.commit()
        late_id = late.id
    assert memory.folder_of(late_id) == 'pip-2'  # a later buddy's: from its name, never `marcel`
    assert memory.folder_of(late_id) == 'pip-2'  # and the same next time


def old_hub(root: Path) -> None:
    """A memory folder as a hub from before 0.7 left it: everything at the top, committed."""
    before = MemoryRepo(root)
    before.write('MEMORY.md', 'The kids eat at 18:00.\n', None, 'app')
    before.write('memory/2026-10-04.md', 'Dentist on Thursday.\n', None, 'app')


def test_a_move_stopped_by_git_finishes_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'memory'
    old_hub(root)
    stopped = MemoryRepo(root, owner_folder=lambda: 'marcel')
    real_git = stopped._git

    def no_commit(*args: str, ok: tuple[int, ...] = (0,)) -> str | None:
        if args[0] == 'commit':
            raise MemoryUnavailable('git commit failed: disk full')
        return real_git(*args, ok=ok)

    monkeypatch.setattr(stopped, '_git', no_commit)
    with pytest.raises(MemoryUnavailable):
        stopped.tree()
    # Moved and linked already: the brain still finds its notes through the old place.
    assert (root / 'MEMORY.md').is_symlink()
    assert (root / 'MEMORY.md').read_text() == 'The kids eat at 18:00.\n'

    again = MemoryRepo(root, owner_folder=lambda: 'marcel')
    assert again.for_agent('Marcel').read('MEMORY.md')['content'] == 'The kids eat at 18:00.\n'
    history = git(root, 'log', '--follow', '--format=%s', '--', 'agents/marcel/MEMORY.md')
    assert history.splitlines()[-1] == 'memory: MEMORY.md (app)'
    assert git(root, 'status', '--porcelain', '--', 'agents').strip() == ''
    assert 'MEMORY.md' not in git(root, 'ls-files').split()  # the link never enters git


def test_a_link_lost_in_a_crash_is_made_again(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    old_hub(root)
    MemoryRepo(root, owner_folder=lambda: 'marcel').tree()
    (root / 'MEMORY.md').unlink()  # stopped right after the move, before the link
    MemoryRepo(root, owner_folder=lambda: 'marcel').tree()
    assert (root / 'MEMORY.md').is_symlink()


def test_an_empty_memory_made_by_render_gives_way_to_the_link(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    old_hub(root)
    MemoryRepo(root, owner_folder=lambda: 'marcel').tree()
    (root / 'MEMORY.md').unlink()
    (root / 'MEMORY.md').write_text('')  # brain/render.py: "an empty MEMORY.md when missing"
    MemoryRepo(root, owner_folder=lambda: 'marcel').tree()
    assert (root / 'MEMORY.md').is_symlink()
    assert (root / 'MEMORY.md').read_text() == 'The kids eat at 18:00.\n'


def test_the_brains_first_notes_after_the_upgrade_are_moved_first(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    old_hub(root)
    fresh = MemoryRepo(root, owner_folder=lambda: 'marcel')
    found = fresh.for_agent('Marcel').read_slice('MEMORY.md')  # what the brain's seed reads
    assert found is not None and found[0] == 'The kids eat at 18:00.\n'


def test_saves_at_the_same_moment_as_the_move_all_land(tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    old_hub(root)
    repo = MemoryRepo(root, owner_folder=lambda: 'marcel')
    errors: list[BaseException] = []

    def save(n: int) -> None:
        try:
            repo.for_agent('Marcel').write(f'memory/n{n}.md', f'{n}\n', None, 'app')
        except BaseException as exc:  # noqa: BLE001 - the test reports any of them
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(f['path'] for f in repo.for_agent('Marcel').tree()) == [
        'MEMORY.md', 'memory/2026-10-04.md', *[f'memory/n{n}.md' for n in range(6)],
    ]  # fmt: skip
