"""Memory per person (S-17.4, B-20, B-38).

Each agent's memory lives under `agents/<folder>/` of the one memory repo, and every way in goes
through the caller's own agent: the app (`/api/memory`), the brain (`/brain/memory`), a worker's
notes, and a project's notes in a worker's briefing. The owner's memory from before 0.7 is moved
under `agents/<owner's folder>/` once, with its git history.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session
from test_brain_api import Brain
from test_channel_fakes import OWNER, ChannelHub, channel_hub, session_id
from test_tasks_fakes import run

from marcel_hub.memory import InvalidMemoryPath, MemoryRepo
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
    User,
    UserRole,
)

EMMA = 'usr_emma'


class Family:
    """The owner (the fake hub's agent, folder = its id) and Emma, whose helper Pip has slug `pip`,
    each with a phone and a brain session."""

    def __init__(self, hub: ChannelHub) -> None:
        self.hub = hub
        with Session(hub.engine) as s:
            s.add(User(id=EMMA, name='Emma', role=UserRole.member))
            pip = Agent(owner_user_id=EMMA, name='Pip', animal='fox', palette='marcel', seed='Pip',
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
        self.emma_phone = {'Authorization': f'Bearer {hub.phone(EMMA)}'}
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


def test_the_app_shows_each_person_only_their_own_memory(family: Family) -> None:
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
    owners = family.hub.agent_id.lower()
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
    owners = family.root / 'agents' / family.hub.agent_id.lower() / 'projects' / 'inbox.md'
    assert not owners.exists() or 'Homework' not in owners.read_text()


def test_a_worker_starts_with_its_own_agents_project_notes(family: Family) -> None:
    owners = family.root / 'agents' / family.hub.agent_id.lower() / 'projects'
    owners.mkdir(parents=True)
    (owners / 'inbox.md').write_text('The owner bank PIN is 9876.\n')  # same slug, other person
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


def test_the_owners_folder_is_their_agents_slug(family: Family) -> None:
    memory = family.hub.app.state.memory
    assert memory.folder_of(family.pip) == 'pip'
    assert memory.owner_folder() == family.hub.agent_id.lower()  # no slug yet: its id
    with Session(family.hub.engine) as s:
        assert s.get(Agent, family.hub.agent_id).owner_user_id == OWNER  # type: ignore[union-attr]
