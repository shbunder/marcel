"""CLI for the operator surface — ``python -m marcel_core.ops <command>``.

Called by the ``make`` targets (add-user, remove-user, telegram-setup,
doctor) and by ``setup.sh``. Human-readable output, scriptable exit codes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='marcel-ops', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p_add = sub.add_parser('add-user', help='onboard a user (profile, role, memory dir)')
    p_add.add_argument('--user', required=True)
    p_add.add_argument('--role', default='user', choices=('admin', 'user'))

    p_rm = sub.add_parser('remove-user', help='offboard a user by archiving (never deleting)')
    p_rm.add_argument('--user', required=True)

    p_tg = sub.add_parser('telegram-setup', help='register + verify the Telegram webhook')
    p_tg.add_argument('--url', required=True, help='public https base URL Telegram will reach')

    sub.add_parser('doctor', help='report install/runtime health (exit 0 when healthy)')

    args = parser.parse_args(argv)

    from marcel_core.ops import add_user, remove_user, run_doctor, setup_webhook
    from marcel_core.ops.telegram import TelegramSetupError
    from marcel_core.ops.users import UserOpError

    try:
        if args.command == 'add-user':
            print(add_user(args.user, args.role))
        elif args.command == 'remove-user':
            print(remove_user(args.user))
        elif args.command == 'telegram-setup':
            print(asyncio.run(setup_webhook(args.url)))
        elif args.command == 'doctor':
            code, report = asyncio.run(run_doctor())
            print(report)
            return code
    except (UserOpError, TelegramSetupError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
