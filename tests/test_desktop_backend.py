"""The native desktop bridge rejects bad commands before touching user state."""

import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def bridge(monkeypatch):
    pytest.importorskip("gi")
    if sys.platform != "win32":
        monkeypatch.setitem(sys.modules, "nabria.windows.desktop",
                            SimpleNamespace(HOTKEYS={}, Instance=object))
    module = importlib.import_module("nabria.windows.backend")
    yield module
    if sys.platform != "win32":
        sys.modules.pop("nabria.windows.backend", None)


@pytest.mark.parametrize("key,value", [
    ("server_binary", "untrusted.exe"), ("keep_audio", "false"),
    ("language", "unsupported"), ("ui_language", None), ("vocabulary", []),
])
def test_invalid_setting_cannot_reach_config(bridge, key, value):
    replies, writes = [], []
    owner = SimpleNamespace(emit=lambda **data: replies.append(data),
                            _apply_setting=lambda *args: writes.append(args))
    bridge.Backend._command(owner, {"id": 7, "command": "set", "key": key, "value": value})
    assert not writes
    assert replies[-1]["id"] == 7
    assert replies[-1]["ok"] is False


def test_microphone_failure_does_not_hide_existing_models(bridge, monkeypatch, tmp_path):
    found = tmp_path / "ggml-base.bin"
    monkeypatch.setattr(bridge.gpu, "plan", lambda mode: SimpleNamespace(use_gpu=False))
    def missing():
        raise bridge.audio.AudioError("No input device")
    monkeypatch.setattr(bridge.audio, "sources", missing)
    monkeypatch.setattr(bridge.models, "search", lambda **kwargs: [SimpleNamespace(path=found)])
    scheduled = []
    monkeypatch.setattr(bridge.GLib, "idle_add", lambda callback: scheduled.append(callback))
    owner = SimpleNamespace(log=lambda message: None, _ready=lambda: None)
    bridge.Backend._inventory(owner)
    assert owner.device_list == []
    assert owner.found_models == [{"path": str(found), "name": found.name}]
    assert scheduled == [owner._ready]
