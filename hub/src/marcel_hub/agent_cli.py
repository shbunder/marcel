"""`marcel-hub agent create|list`: make the owner's agent, so the hub starts a brain for it.

A stop-gap until `marcel-hub setup` (S-17.1) makes the owner and their agent; `setup` must reuse an
agent made here (same owner, same name) rather than make a second one.
"""

import argparse
from pathlib import Path

from sqlmodel import Session, select

from marcel_hub import db
from marcel_hub.app import SettingsError, load_settings
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent


def agent(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='marcel-hub agent', description="The owner's agents.")
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help="make the owner's agent (nothing changes if it exists)")
    create.add_argument(
        '--name', default='Marcel', help='what the family calls it (default Marcel)'
    )
    create.add_argument('--animal', default='giraffe', help='a recipe in shared/avatar/recipes/')
    create.add_argument('--palette', default='marcel', help='a palette id (default marcel)')
    create.add_argument('--seed', help="the avatar's details (default: the name)")
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
            made = Agent(
                owner_user_id=OWNER_USER_ID,
                name=args.name,
                animal=args.animal,
                palette=args.palette,
                seed=args.seed or args.name,
                persona_path=str(Path(settings.brain_cwd) / 'CLAUDE.md')
                if settings.brain_cwd
                else None,
            )
            session.add(made)
            session.commit()
            ensure_main_conversation(session, made)
            print(f'Made {made.name} ({made.id}), with its main conversation and Inbox.')
            if settings.brain_cwd is None:
                print('MARCEL_BRAIN_CWD is not set, so the hub will not start its brain.')
            else:
                print(f'The hub starts its brain in {settings.brain_cwd} within a few seconds.')
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
