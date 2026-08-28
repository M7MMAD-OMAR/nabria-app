"""The app id, and the three files that have to agree with it.

None of this is cosmetic. xdg-desktop-portal identifies a host application by
its systemd unit name, so the unit must be `app-<app-id>.service` or the
GlobalShortcuts portal refuses to bind anything -- silently, with the daemon
running normally and the key simply never firing.

That coupling was documented in three comments and checked by nothing.
"""

from __future__ import annotations

import configparser
import re
import subprocess
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
    """One version, restated in three files that no single build reads together.

    __version__ said 0.1.0 with v0.2.0 tagged and nothing noticed, which is how
    this class of drift ends: not with a failure, but with a package whose
    number means nothing. Restating rather than deriving is forced by the
    ecosystems -- Copr builds the in-repo spec and the AUR compares a literal
    pkgver -- so the binding has to be a test.

    Matched by field, not by exact spacing, so reformatting the spec does not
    fail with a message about drift that is not drift.
    """
    from nabria import __version__

    spec = (PACKAGING / "nabria.spec").read_text(encoding="utf-8")
    pkgbuild = (PACKAGING / "PKGBUILD").read_text(encoding="utf-8")
    assert re.search(rf"^Version:\s+{re.escape(__version__)}$", spec, re.M)
    assert re.search(rf"^pkgver={re.escape(__version__)}$", pkgbuild, re.M)


def test_the_engine_release_is_the_one_pinned_in_engine_version():
    # Both packages bundle the engine by naming its release. engine/VERSION is
    # the source of truth for that name, and a package pinning an older one
    # would ship a binary whose checksum the installer no longer recognises.
    # Expanded by a shell rather than by reimplementing $VAR substitution here.
    # engine/VERSION is a shell file that every script sources, so this asks the
    # same mechanism they do -- a hand-rolled parser would have to be taught
    # about each new variable, and about comments and quoting.
    release = subprocess.run(
        ["sh", "-c", f'. "{ROOT}/engine/VERSION"; printf %s "$ENGINE_RELEASE"'],
        capture_output=True, text=True, check=True,
    ).stdout

    assert re.search(rf"^%global engine\s+{re.escape(release)}$",
                     (PACKAGING / "nabria.spec").read_text(encoding="utf-8"), re.M)
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
    assert re.search(r"^Recommends:\s+gtk4-layer-shell$", spec, re.M)
    assert not re.search(r"^Requires:\s+gtk4-layer-shell$", spec, re.M)

    pkgbuild = (PACKAGING / "PKGBUILD").read_text(encoding="utf-8")
    assert "optdepends=('gtk4-layer-shell:" in pkgbuild


def test_the_packaged_layout_installs_the_unit_under_the_app_id():
    # packaging/layout.sh is shared by every format, so this one assertion
    # covers the rpm, the deb and the AUR package at once -- and the app id it
    # builds the unit name from is the thing the shortcuts portal reads.
    layout = (PACKAGING / "layout.sh").read_text(encoding="utf-8")
    assert f"APP_ID={config.APP_ID}" in layout
    assert "UNIT_NAME=app-$APP_ID.service" in layout
    # The launcher goes to /usr/bin, and the code it has to find goes to
    # /usr/lib/nabria -- the pair run.sh resolves when it is not in a checkout.
    # Destinations only: asserting whole command lines breaks on reformatting
    # without anything having changed.
    assert '"$destdir/usr/bin/nabria"' in layout
    assert '"$destdir/usr/lib/nabria/nabria"' in layout


def test_the_packaging_files_name_the_repository_common_sh_names():
    """A .spec and a PKGBUILD cannot source shell helpers, so this is the bind.

    Both build their download URLs from that slug -- Source0 and the source=
    array -- so a rename does not leave a stale metadata field, it makes Copr
    and `yay -S nabria` fail to fetch anything at all.
    """
    common = (ROOT / "scripts" / "common.sh").read_text(encoding="utf-8")
    slug = re.search(r"^NABRIA_REPO=(\S+)$", common, re.M).group(1)
    for name in ("nabria.spec", "PKGBUILD", "debian-control"):
        assert slug in (PACKAGING / name).read_text(encoding="utf-8"), name


def test_the_packages_name_the_engine_artifact_pinned_in_engine_version():
    # The spec's Source1 and the PKGBUILD's source= both end in this filename,
    # and neither can read engine/VERSION. Renaming the asset would leave every
    # local build passing while Copr and the AUR fetch a 404.
    artifact = subprocess.run(
        ["sh", "-c", f'. "{ROOT}/engine/VERSION"; printf %s "$ENGINE_ARTIFACT"'],
        capture_output=True, text=True, check=True,
    ).stdout
    for name in ("nabria.spec", "PKGBUILD"):
        assert artifact in (PACKAGING / name).read_text(encoding="utf-8"), name
    # And it is the name release-engine.sh actually writes into engine/CHECKSUMS,
    # which is what install.sh and package.sh look the download up by.
    assert (ROOT / "engine" / "CHECKSUMS").read_text(encoding="utf-8").split()[1] == artifact
