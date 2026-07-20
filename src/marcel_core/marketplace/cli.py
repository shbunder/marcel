"""Terminal path for the marketplace flows (FR5) — behind the make targets.

Same flows as the conversational tool; the review gate here is the
interactive y/N prompt (or ``--yes`` for scripted use, which still renders
the summary first so the terminal scrollback carries the review).
"""

from __future__ import annotations

import argparse
import sys

_WHO = {'user_slug': 'zoo-keeper', 'channel': 'terminal'}


def _confirm(summary: str, *, assume_yes: bool) -> bool:
    print(summary)
    if assume_yes:
        print('(--yes given — proceeding)')
        return True
    answer = input('Proceed? [y/N] ').strip().lower()
    return answer in ('y', 'yes')


def main(argv: list[str] | None = None) -> int:
    from marcel_core.marketplace import installer
    from marcel_core.marketplace.fetchers import FetchError, browse_source
    from marcel_core.marketplace.sources import get_source, load_sources

    parser = argparse.ArgumentParser(prog='marcel-marketplace', description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('sources')
    p_browse = sub.add_parser('browse')
    p_browse.add_argument('--source', required=True)
    p_install = sub.add_parser('install')
    p_install.add_argument('--source', required=True)
    p_install.add_argument('--name', required=True)
    p_install.add_argument('--user', default=None, help='place under users/<slug>/ instead of global')
    p_install.add_argument('--yes', action='store_true')
    p_update = sub.add_parser('update')
    p_update.add_argument('--kind', required=True, choices=('skill', 'connector'))
    p_update.add_argument('--name', required=True)
    p_update.add_argument('--yes', action='store_true')
    p_remove = sub.add_parser('remove')
    p_remove.add_argument('--kind', required=True, choices=('skill', 'connector'))
    p_remove.add_argument('--name', required=True)
    for verb in ('enable', 'disable'):
        p_en = sub.add_parser(verb)
        p_en.add_argument('--kind', required=True, choices=('skill', 'connector'))
        p_en.add_argument('--name', required=True)
        p_en.add_argument('--user', required=True)
    args = parser.parse_args(argv)

    try:
        if args.action == 'sources':
            for entry in load_sources():
                print(f'{entry.name}\t{entry.type.value}\t{entry.description or entry.url}')
        elif args.action == 'browse':
            for c in browse_source(get_source(args.source)):
                scripts = ' [scripts]' if c.has_scripts else ''
                print(f'{c.name}\t{c.kind}\t{c.description}{scripts}')
        elif args.action == 'install':
            review = installer.review_install(args.source, args.name, **_WHO)
            if not _confirm(review.summary, assume_yes=args.yes):
                print('aborted')
                return 1
            result = installer.install(args.source, args.name, review.token, placement_slug=args.user, **_WHO)
            print(f'installed: {result.path} (commit {result.commit[:12]}; enablement: {result.seeded})')
        elif args.action == 'update':
            review = installer.review_update(args.kind, args.name, **_WHO)
            if not _confirm(review.summary, assume_yes=args.yes):
                print('aborted')
                return 1
            result = installer.update(args.kind, args.name, review.token, **_WHO)
            print(f'updated: {result.path} (commit {result.commit[:12]})')
        elif args.action == 'remove':
            commit = installer.remove(args.kind, args.name, **_WHO)
            print(f'removed (commit {commit[:12]})')
        elif args.action in ('enable', 'disable'):
            from marcel_core.marketplace import enablement

            fn = enablement.enable if args.action == 'enable' else enablement.disable
            print(fn(f'{args.kind}s', args.name, args.user))
    except (installer.InstallError, FetchError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
