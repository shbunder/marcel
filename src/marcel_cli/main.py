"""Marcel CLI entrypoint."""
from __future__ import annotations

import argparse

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
    args = parser.parse_args()

    config = load_config(host=args.host, port=args.port, user=args.user)

    from .app import MarcelApp  # late import so tests can import main without textual
    app = MarcelApp(config)
    app.run()


if __name__ == '__main__':
    main()
