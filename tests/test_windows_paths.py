"""Windows paths are testable without importing any native Windows APIs."""

import importlib


def test_windows_import_does_not_require_unix_user_ids(tmp_path, monkeypatch):
    from nabria import config
    import os
    import sys

    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        patch.delattr(os, "getuid", raising=False)
        patch.setenv("APPDATA", str(tmp_path / "roaming"))
        patch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        patch.setenv("NABRIA_INSTALL_DIR", str(tmp_path / "installed"))
        try:
            importlib.reload(config)
            assert config.CONFIG_DIR == tmp_path / "roaming/Nabria"
            assert config.DATA_DIR == tmp_path / "local/Nabria"
            assert config.DEFAULTS["server_binary"] == str(tmp_path / "installed/engine/whisper-server.exe")
            assert config.RUNTIME_DIR == config.STATE_DIR
        finally:
            patch.undo()
            importlib.reload(config)
