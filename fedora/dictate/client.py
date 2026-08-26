"""Talks to the running daemon over its Unix socket.

Deliberately imports nothing but the standard library. The hotkey runs this
path on every press, and pulling in GTK just to write eight bytes to a socket
would put a visible delay between the keypress and the orb appearing.
"""

from __future__ import annotations

import socket
import sys

from .config import SOCKET_PATH

COMMANDS = ("toggle", "start", "stop", "cancel", "status", "last", "quit")


def send(command: str, timeout: float = 5.0) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(SOCKET_PATH))
        connection.sendall(command.encode("utf-8"))
        connection.shutdown(socket.SHUT_WR)
        # Read to EOF: a `last` reply is a whole transcript, not one packet.
        chunks = []
        while True:
            chunk = connection.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace").strip()


def main(command: str) -> int:
    try:
        print(send(command))
        return 0
    except (FileNotFoundError, ConnectionRefusedError):
        print(
            "dictate: daemon is not running (systemctl --user start dictate)",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"dictate: {exc}", file=sys.stderr)
        return 1
