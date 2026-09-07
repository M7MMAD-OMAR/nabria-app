"""Authenticated local named pipes. Only byte messages, never pickle payloads."""

from __future__ import annotations

import hashlib
import os
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener

from .. import config


def address() -> str:
    identity = hashlib.sha256(str(config.CONFIG_DIR).casefold().encode()).hexdigest()[:24]
    return rf"\\.\pipe\nabria-{identity}"


def _key(create: bool = False) -> bytes:
    path = config.STATE_DIR / "control.key"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(os.urandom(32))
        except FileExistsError:
            pass
    return path.read_bytes()


def send(command: str, timeout: float = 5.0) -> str:
    with Client(address(), family="AF_PIPE", authkey=_key()) as connection:
        connection.send_bytes(command.encode("utf-8"))
        if not connection.poll(timeout):
            raise TimeoutError("Nabria did not answer")
        return connection.recv_bytes().decode("utf-8")


def serve(dispatch, log):
    listener = Listener(address(), family="AF_PIPE", authkey=_key(create=True))

    def run():
        while True:
            try:
                with listener.accept() as connection:
                    if connection.poll(5):
                        command = connection.recv_bytes(64).decode("utf-8", "replace")
                        connection.send_bytes(dispatch(command).encode("utf-8"))
            except (OSError, EOFError, ValueError, AuthenticationError) as exc:
                log(f"control connection failed: {exc}")

    threading.Thread(target=run, daemon=True, name="nabria-control").start()
    return listener
