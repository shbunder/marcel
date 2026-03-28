"""Marcel CLI entrypoint."""
from __future__ import annotations

import argparse
import asyncio

from .config import load_config


def main() -> None:
    """Parse flags, load config, and launch the TUI."""
    parser = argparse.ArgumentParser(
        prog='marcel',
        description='Marcel — personal agent TUI',
    )
    parser.add_argument('--host', metavar='HOST', help='Marcel server hostname')
    parser.add_argument('--port', type=int, metavar='PORT', help='Marcel server port')
    parser.add_argument('--user', metavar='USER', help='User slug')
    parser.add_argument('--model', metavar='MODEL', help='Claude model to use')
    args = parser.parse_args()

    config = load_config(host=args.host, port=args.port, user=args.user, model=args.model)

    from .app import run
    asyncio.run(run(config))


if __name__ == '__main__':
    main()
