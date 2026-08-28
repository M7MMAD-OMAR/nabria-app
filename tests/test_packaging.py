"""The app id, and the three files that have to agree with it.

None of this is cosmetic. xdg-desktop-portal identifies a host application by
its systemd unit name, so the unit must be `app-<app-id>.service` or the
GlobalShortcuts portal refuses to bind anything -- silently, with the daemon
running normally and the key simply never firing.

That coupling was documented in three comments and checked by nothing.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from nabria import config

ROOT = Path(__file__).resolve().parent.parent


def test_the_unit_is_named_after_the_app_id():
    assert (ROOT / "systemd" / f"app-{config.APP_ID}.service").is_file()


def test_there_is_exactly_one_unit():
    # A leftover unit under the old name would be installed alongside and the
    # daemon would be started twice, or under the wrong name.
    units = sorted(path.name for path in (ROOT / "systemd").glob("*.service"))
    assert units == [f"app-{config.APP_ID}.service"]


def test_the_installer_writes_that_unit_name():
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert f"unit_name=app-{config.APP_ID}.service" in installer


def test_the_desktop_entry_matches_the_app_id():
    entry = ROOT / "share" / f"{config.APP_ID}.desktop"
    assert entry.is_file()

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(entry.read_text(encoding="utf-8"))
    section = parser["Desktop Entry"]
    # Compositors tie the window to this entry through the app id the daemon
    # registers; a mismatch loses the icon and the portal's identification.
    assert section["StartupWMClass"] == config.APP_ID
    assert section["Icon"] == config.APP_ID


def test_the_unit_runs_the_launcher_through_a_placeholder():
    # Written out literally once, which meant the unit only worked on the
    # machine it was written on.
    unit = (ROOT / "systemd" / f"app-{config.APP_ID}.service").read_text(encoding="utf-8")
    assert "ExecStart=@RUN_SH@ daemon" in unit
