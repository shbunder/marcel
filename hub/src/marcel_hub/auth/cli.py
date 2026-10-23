"""`marcel-hub pair`: make a one-time pairing code and print it as a QR code. With
`--buddy <slug>` the phone it pairs starts on that buddy (its link carries `&buddy=`)."""

import argparse
import io

import segno
from sqlmodel import Session, col, or_, select

from marcel_hub import db
from marcel_hub.app import SettingsError, load_settings
from marcel_hub.auth.tokens import (
    NO_PUBLIC_URL,
    PAIRING_CODE_TTL,
    create_pairing_code,
    hub_api,
    pairing_url,
)
from marcel_hub.models import Agent


def pair(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='marcel-hub pair', description='Show a QR code that pairs a phone with this hub.'
    )
    parser.add_argument(
        '--hub', default=None, help='the URL the phone uses (default <public_url>/api)'
    )
    parser.add_argument(
        '--buddy',
        default=None,
        help='the buddy the phone starts on: its slug (as in ~/marcel/brains/<slug>) or its id '
        '(default the first one)',
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'No pairing code was made. {exc}') from exc
    hub = args.hub or (hub_api(settings.public_url) if settings.public_url else None)
    if hub is None:
        raise SystemExit(f'No pairing code was made. {NO_PUBLIC_URL}')

    engine = db.make_engine(settings.db_path)
    try:
        db.upgrade(engine)
        with Session(engine) as session:
            buddy = _buddy(session, args.buddy) if args.buddy else None
            code = create_pairing_code(session, agent_id=buddy)
    finally:
        engine.dispose()

    url = pairing_url(hub, code, buddy)
    qr = io.StringIO()
    segno.make(url, error='m').terminal(out=qr, compact=True)
    minutes = int(PAIRING_CODE_TTL.total_seconds() // 60)
    print(qr.getvalue())
    print(f'Scan this with the Marcel app, or type the code {code}.')
    print(f'It works once, for {minutes} minutes.')
    print(url)
    return 0


def _buddy(session: Session, wanted: str) -> str:
    """The id of the live buddy whose slug or id is `wanted`; stops with the live ones when none."""
    live = session.exec(
        select(Agent).where(col(Agent.archived_at).is_(None)).order_by(col(Agent.id))
    ).all()
    found = session.exec(
        select(Agent).where(
            col(Agent.archived_at).is_(None), or_(Agent.slug == wanted, Agent.id == wanted)
        )
    ).first()
    if found is None:
        names = ', '.join(a.slug or a.id for a in live) or 'none yet'
        raise SystemExit(
            f'No pairing code was made. There is no buddy "{wanted}". Buddies: {names}.'
        )
    return found.id
