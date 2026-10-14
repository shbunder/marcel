"""`marcel-hub agent create|list`: list the owner's agents, or make an extra one.

`marcel-hub setup` (S-17.1, `users/cli.py`) makes the owner and their first agent and reuses one
made here; `create` stays for a second agent (`--another`).
"""

import argparse
import os
from pathlib import Path

from sqlmodel import Session, select

from marcel_hub import db
from marcel_hub.app import SettingsError, load_settings
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent, Conversation, ConversationKind
from marcel_hub.users.store import OWNER_AGENT_SLUG, slug_for


def agent(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='marcel-hub agent', description="The owner's agents.")
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help="make the owner's agent (nothing changes if it exists)")
    default_name = os.environ.get('MARCEL_AGENT_NAME') or 'Marcel'
    create.add_argument(
        '--name',
        default=default_name,
        help=f'what the family calls it (default MARCEL_AGENT_NAME, else Marcel: {default_name})',
    )
    create.add_argument('--animal', default='giraffe', help='a recipe in shared/avatar/recipes/')
    create.add_argument('--palette', default='marcel', help='a palette id (default marcel)')
    create.add_argument('--seed', help="the avatar's details (default: the name)")
    create.add_argument(
        '--another',
        action='store_true',
        help='make it even though the owner has an agent already (each agent gets its own brain)',
    )
    sub.add_parser('list', help="list the owner's agents")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'Nothing was changed. {exc}') from exc

    engine = db.make_engine(settings.db_path)
    try:
        db.upgrade(engine)
        with Session(engine) as session:
            if args.command == 'list':
                return _list(session)
            found = session.exec(
                select(Agent).where(Agent.owner_user_id == OWNER_USER_ID, Agent.name == args.name)
            ).first()
            if found is not None:
                ensure_main_conversation(session, found)
                print(f'{found.name} already exists ({found.id}). Nothing was changed.')
                return 0
            other = session.exec(select(Agent).where(Agent.owner_user_id == OWNER_USER_ID)).first()
            if other is not None and not args.another:
                print(
                    f'The owner already has {other.name} ({other.id}), so nothing was made: a '
                    'second agent starts a second brain. Check the name, or add --another.'
                )
                return 1
            # The owner's first agent is `marcel` (its brain keeps `brain_cwd`); another gets a slug
            # of its own, and with it its own brain folder and memory (S-17.7).
            taken = session.exec(select(Agent).where(Agent.slug == OWNER_AGENT_SLUG)).first()
            slug = slug_for(session, args.name) if other is not None or taken else OWNER_AGENT_SLUG
            made = Agent(
                owner_user_id=OWNER_USER_ID,
                name=args.name,
                slug=slug,
                animal=args.animal,
                palette=args.palette,
                seed=args.seed or args.name,
                persona_path=str(Path(settings.brain_cwd) / 'CLAUDE.md')
                if settings.brain_cwd
                else None,
            )
            # The agent and its main conversation in one commit: the hub's brain keeper, in another
            # process, never sees the agent without it (and so never makes a second one).
            session.add(made)
            session.flush()
            main = Conversation(agent_id=made.id, kind=ConversationKind.main)
            session.add(main)
            session.flush()
            made.main_conversation_id = main.id
            session.add(made)
            session.commit()
            print(f'Made {made.name} ({made.id}), with its main conversation and Inbox.')
            if settings.brain_cwd is None:
                print('MARCEL_BRAIN_CWD is not set, so the hub will not start its brain.')
            else:
                print(
                    f'The hub starts its brain in {settings.brain_cwd} once the hub and the runner '
                    'are up. Check with: make -C deploy health'
                )
            return 0
    finally:
        engine.dispose()


def _list(session: Session) -> int:
    rows = session.exec(select(Agent).where(Agent.owner_user_id == OWNER_USER_ID)).all()
    if not rows:
        print('No agents yet. Make one with: marcel-hub agent create --name Marcel')
    for row in rows:
        print(f'{row.id}  {row.name}  {row.animal}/{row.palette}  main {row.main_conversation_id}')
    return 0
