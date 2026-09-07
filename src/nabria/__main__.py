"""Entry point. `python3 -m nabria daemon` runs the service; anything else is
a control command, dispatched without ever importing GTK."""

from __future__ import annotations

import sys

from .client import COMMANDS

USAGE = f"usage: python3 -m nabria [daemon|{'|'.join(COMMANDS)}]"


def main() -> int:
    if sys.platform == "win32" and sys.argv[1:] == ["--desktop-backend"]:
        from .windows.backend import main as desktop_backend
        return desktop_backend()
    if sys.platform == "win32" and sys.argv[1:] == ["--self-test"]:
        from .windows.selftest import main as self_test
        return self_test()
    command = sys.argv[1] if len(sys.argv) > 1 else ("daemon" if sys.platform == "win32" else "toggle")
    instance = None
    if sys.platform == "win32" and command == "daemon":
        from .windows.desktop import Instance
        instance = Instance()
        if not instance.primary:
            from .client import main as run_client
            instance.close()
            return run_client("settings")
    if command == "daemon":
        from .app import main as run_daemon

        try:
            return run_daemon()
        finally:
            if instance is not None:
                instance.close()
    if command in COMMANDS:
        from .client import main as run_client

        return run_client(command)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
