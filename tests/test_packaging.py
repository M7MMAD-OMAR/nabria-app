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


# --------------------------------------------------------------- the packages

# The distribution packages restate three things the source already knows: the
# version, the layout, and which dependencies are optional. Each of those has a
# silent failure mode, so each gets a test rather than a comment.

PACKAGING = ROOT / "packaging"


def test_every_package_claims_the_version_in_the_source():
    """One version, written in four files that no single build reads together.

    __version__ said 0.1.0 with v0.2.0 tagged and nothing noticed, which is how
    this class of drift ends: not with a failure, but with a package whose
    number means nothing.
    """
    from nabria import __version__

    spec = (PACKAGING / "nabria.spec").read_text(encoding="utf-8")
    pkgbuild = (PACKAGING / "PKGBUILD").read_text(encoding="utf-8")
    assert f"\nVersion:        {__version__}\n" in spec
    assert f"\npkgver={__version__}\n" in pkgbuild


def test_the_engine_release_is_the_one_pinned_in_engine_version():
    # Both packages bundle the engine by naming its release. engine/VERSION is
    # the source of truth for that name, and a package pinning an older one
    # would ship a binary whose checksum the installer no longer recognises.
    pinned = dict(
        line.split("=", 1)
        for line in (ROOT / "engine" / "VERSION").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    release = pinned["ENGINE_RELEASE"].replace(
        "$WHISPER_CPP_VERSION", pinned["WHISPER_CPP_VERSION"]
    ).replace("$ENGINE_REVISION", pinned["ENGINE_REVISION"])

    assert f"%global engine   {release}" in (PACKAGING / "nabria.spec").read_text(encoding="utf-8")
    assert f"_engine={release}" in (PACKAGING / "PKGBUILD").read_text(encoding="utf-8")


def test_layer_shell_is_never_a_hard_dependency():
    """Ubuntu 24.04 does not package gtk4-layer-shell at all.

    As a Depends the package would be uninstallable there rather than merely
    degraded -- and the fallback to an ordinary window exists precisely so that
    a missing layer shell costs the indicator's stacking and nothing else.
    """
    control = (PACKAGING / "debian-control").read_text(encoding="utf-8")
    depends = next(line for line in control.splitlines() if line.startswith("Depends:"))
    recommends = next(line for line in control.splitlines() if line.startswith("Recommends:"))
    assert "layer-shell" not in depends
    assert "layer-shell" in recommends

    spec = (PACKAGING / "nabria.spec").read_text(encoding="utf-8")
    assert "Recommends:     gtk4-layer-shell" in spec
    assert "Requires:       gtk4-layer-shell" not in spec

    pkgbuild = (PACKAGING / "PKGBUILD").read_text(encoding="utf-8")
    assert "gtk4-layer-shell" in pkgbuild.split("optdepends=")[1].split(")")[0]


def test_the_packaged_layout_installs_the_unit_under_the_app_id():
    # packaging/layout.sh is shared by every format, so this one assertion
    # covers the rpm, the deb and the AUR package at once -- and the app id it
    # builds the unit name from is the thing the shortcuts portal reads.
    layout = (PACKAGING / "layout.sh").read_text(encoding="utf-8")
    assert f"APP_ID={config.APP_ID}" in layout
    assert "UNIT_NAME=app-$APP_ID.service" in layout
    # The launcher goes to /usr/bin, and the code it has to find goes to
    # /usr/lib/nabria -- the pair run.sh resolves when it is not in a checkout.
    assert 'install -m 755 "$source_tree/scripts/run.sh" "$destdir/usr/bin/nabria"' in layout
    assert 'install -d "$destdir/usr/lib/nabria/nabria"' in layout
