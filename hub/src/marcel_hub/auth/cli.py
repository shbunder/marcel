"""`marcel-hub pair`: make a one-time pairing code and print it as a QR code."""

import argparse
import io

import segno
from sqlmodel import Session

from marcel_hub import db
from marcel_hub.app import SettingsError, load_settings
from marcel_hub.auth.tokens import (
    NO_PUBLIC_URL,
    PAIRING_CODE_TTL,
    create_pairing_code,
    hub_api,
    pairing_url,
)


def pair(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='marcel-hub pair', description='Show a QR code that pairs a phone with this hub.'
    )
    parser.add_argument(
        '--hub', default=None, help='the URL the phone uses (default <public_url>/api)'
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
            code = create_pairing_code(session)
    finally:
        engine.dispose()

    url = pairing_url(hub, code)
    qr = io.StringIO()
    segno.make(url, error='m').terminal(out=qr, compact=True)
    minutes = int(PAIRING_CODE_TTL.total_seconds() // 60)
    print(qr.getvalue())
    print(f'Scan this with the Marcel app, or type the code {code}.')
    print(f'It works once, for {minutes} minutes.')
    print(url)
    return 0
