"""`marcel-hub setup`: make the owner and their agent on a fresh NUC (B-37). Safe to run again.

The owner is the person whose Claude login the NUC uses (B-38). An agent `marcel-hub agent create`
made earlier is reused, not doubled. When the owner has no phone yet, it prints a QR code that
pairs the first one; `marcel-hub pair` pairs more.
"""

import argparse
import io
import os
from pathlib import Path

import segno
from sqlalchemy import func
from sqlmodel import Session, col, select

from marcel_hub import db
from marcel_hub.app import SettingsError, load_settings
from marcel_hub.auth.tokens import (
    NO_PUBLIC_URL,
    OWNER_USER_ID,
    PAIRING_CODE_TTL,
    create_pairing_code,
    hub_api,
    pairing_url,
)
from marcel_hub.models import Device
from marcel_hub.users.store import ensure_owner, ensure_owner_agent


def setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='marcel-hub setup',
        description="Make the owner and the owner's agent, then pair the owner's first phone.",
    )
    default_name = os.environ.get('MARCEL_AGENT_NAME') or 'Marcel'
    parser.add_argument(
        '--agent-name',
        default=default_name,
        help=f'what the family calls the agent if it is made now (default {default_name})',
    )
    parser.add_argument(
        '--hub', default=None, help='the URL the phone uses (default <public_url>/api)'
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'Nothing was changed. {exc}') from exc

    hub = args.hub or (hub_api(settings.public_url) if settings.public_url else None)
    engine = db.make_engine(settings.db_path)
    try:
        db.upgrade(engine)
        with Session(engine) as session:
            owner = ensure_owner(session, settings.owner_name)
            persona = str(Path(settings.brain_cwd) / 'CLAUDE.md') if settings.brain_cwd else None
            agent, made = ensure_owner_agent(session, args.agent_name, persona)
            print(f'Owner: {owner.name}. Their Claude login on this NUC runs every helper.')
            if made:
                print(f'Made {agent.name} ({agent.id}), with its main conversation and Inbox.')
            else:
                print(f'{agent.name} ({agent.id}) is already here. Nothing was made twice.')
            phones = session.exec(
                select(func.count()).select_from(Device).where(col(Device.user_id) == OWNER_USER_ID)
            ).one()
            code = None if phones or hub is None else create_pairing_code(session)
    finally:
        engine.dispose()

    if settings.brain_cwd is None:
        print('MARCEL_BRAIN_CWD is not set, so the hub will not start a brain.')
    if hub is None and not phones:
        print(f'No phone was paired. {NO_PUBLIC_URL}')
        return 0
    if code is None or hub is None:
        print(f'{owner.name} has {phones} paired phone(s). Pair another with: marcel-hub pair')
        return 0
    url = pairing_url(hub, code)
    qr = io.StringIO()
    segno.make(url, error='m').terminal(out=qr, compact=True)
    minutes = int(PAIRING_CODE_TTL.total_seconds() // 60)
    print(qr.getvalue())
    print(f'Scan this with the Marcel app on your phone, or type the code {code}.')
    print(f'It works once, for {minutes} minutes.')
    print(url)
    return 0
