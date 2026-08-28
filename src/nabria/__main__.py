"""Entry point. `python3 -m nabria daemon` runs the service; anything else is
a control command, dispatched without ever importing GTK."""

from __future__ import annotations

import sys

from .client import COMMANDS

USAGE = f"usage: python3 -m nabria [daemon|{'|'.join(COMMANDS)}]"


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if command == "daemon":
        from .app import main as run_daemon

        return run_daemon()
    if command in COMMANDS:
        from .client import main as run_client

        return run_client(command)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
