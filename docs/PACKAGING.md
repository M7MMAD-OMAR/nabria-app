# Packaging

How Nabria reaches a machine, and why in that order.

## The four ways in

| | command | updates arrive |
|---|---|---|
| `.rpm` | `sudo dnf install …/nabria.rpm` | by re-running the same line |
| `.deb` | `sudo apt install ./nabria.deb` | by re-running the same line |
| `PKGBUILD` | `makepkg -si` in `packaging/` | by pulling and re-running it |
| `curl … \| sh` | anything else | by re-running the same line |

Each of those URLs is `releases/latest/download/…`, so none of the four lines
ever changes. Updating is re-running the line you installed with.

There is no Copr project, no AUR package and no apt repository, and that is a
decision rather than a gap — the sections below are kept because they are what
setting one up would take, not because it is planned. Each buys exactly one
thing, `dnf upgrade` or `yay` noticing a release on its own, and each costs a
hosted identity that has to stay alive: a Fedora account, an AUR account with a
registered SSH key, a GPG signing key sitting wherever the apt repo is built.
The `.spec` and the `PKGBUILD` are kept correct regardless, and
`scripts/release.sh` still rewrites the `PKGBUILD` checksums on every release,
so none of this rots while it is unused.

**Do not run two of them on the same machine.** A user install shadows a
packaged one at every point — the launcher on `PATH`, the unit in
`~/.config/systemd/user`, the desktop entry in `~/.local/share/applications` —
so `dnf install nabria` after `install.sh` does nothing observable. `install.sh`
warns when it finds a packaged copy, and `scripts/install.sh --uninstall`
removes the user half (keeping config, models and transcripts, which are not
this script's to delete).

All four install the same layout and the same bundled engine. `packaging/layout.sh`
is the only place that layout is written down, so an rpm and a deb cannot end
up different in a way only one person ever sees.

## Why native packages at all

Because they are the only path where **updates and update notifications are
free**. `dnf` and `apt` already check, and GNOME Software and KDE Discover
already tell the user. Nothing in Nabria phones home, no self-update code
exists, and none needs to — which is a better answer than any amount of
in-app update machinery, and a much smaller one.

That is also the argument against an AppImage. It looks like the tidy answer —
one file, `gh-releases-zsync` for updates — but bundling `gtk4-layer-shell`
means controlling library load order *inside* a bundle, and the whole reason
`run.sh` exists is that Python cannot control link order at all. An AppImage
that silently loses the layer shell is an AppImage whose indicator can be
covered by a fullscreen window, which is the one failure this project was
written to avoid.

Flatpak is not merely unattractive here, it is impossible — see the README.

## What is in a package and what is not

**In:** the Python, `run.sh` as `/usr/bin/nabria`, the systemd user unit, the
desktop entry, and **the transcription engine**. Bundling the engine is why the
packages are `x86_64` rather than `noarch` — every line of Nabria is Python,
but the engine is a compiled binary and it is worth the arch restriction to
make "installed" mean installed.

**Not in:** the model. Between 148 MB and 1.6 GB depending on hardware, chosen
at first launch by what the machine can actually run. It stays a first-run
download and the wizard handles it.

The engine is verified against `engine/CHECKSUMS` before it goes into a
package. A package is the one artifact a user has no reason to check
themselves, so shipping an unverified binary inside one is worse than shipping
none.

## The dependency that must stay soft

`gtk4-layer-shell` is a **Recommends / optdepends in every format, never a
hard dependency.** Ubuntu 24.04 does not package it at all, so as a `Depends:`
the `.deb` would be uninstallable there rather than merely degraded — and the
fallback to an ordinary window exists precisely so a missing layer shell costs
the indicator's stacking and nothing else.

`tests/test_packaging.py` asserts this for all three formats, because it is a
one-word edit away at any time and the consequence is invisible until an
Ubuntu user tries.

## Building and testing

```sh
scripts/package.sh              # both, into dist/
scripts/check.sh --packages     # install them in clean Fedora, Debian and Ubuntu
```

Each package is built inside a container of the distribution it targets, so
nothing needs `rpmbuild` or `dpkg-deb` on the machine doing the building.

`--packages` is not optional politeness. `Requires:` and `Depends:` are lists
of package names, and this project's rule is that those cannot be reasoned
about, only run — every packaging bug it has ever had came from a container.
Ubuntu 24.04 is in that matrix specifically because it is the machine that
proves the layer-shell dependency is soft.

## Copr — what it would take

Not set up, by the decision above. Written down so the option stays open at the
cost of reading it, not because it is queued. `packaging/nabria.spec` is
suitable as-is: both its sources are URLs, which is what Copr fetches.

1. Sign in at <https://copr.fedorainfracloud.org> and make a project called
   `nabria`, targeting the current Fedora releases, `x86_64` only.
2. Add a package, method **SCM**, pointing at this repository, spec file
   `packaging/nabria.spec`.
3. Add a webhook on the GitHub side so a new tag rebuilds it.

Then the install becomes two lines, and every later update arrives through
`dnf upgrade`:

```sh
sudo dnf copr enable <account>/nabria
sudo dnf install nabria
```

## Debian: why there is no apt repository

An apt repository needs a signed `InRelease`, which means a GPG private key
living wherever the repository is built. Doing that in GitHub Actions puts a
signing key in CI, which is the dependency this project is deliberately without
— the whole testing story is that everything runs locally and CI only confirms
it. Copr sidesteps signing entirely by doing it itself; Debian has no free
equivalent that does.

So the `.deb` is a release asset, and `apt install ./nabria.deb` against a
stable URL is the answer. The openSUSE Build Service could host a signed apt
repository for Debian and Ubuntu, and is where to start if this is ever
revisited.

## When the version changes

Four files say the version and no build reads them together, so
`tests/test_packaging.py` binds them:

```
src/nabria/__init__.py    __version__     the source of truth
packaging/nabria.spec     Version:
packaging/PKGBUILD        pkgver
the git tag               v<version>      checked by scripts/release.sh
```

`scripts/release.sh` rewrites the PKGBUILD's `sha256sums` after uploading and
commits them. They are the checksums of a tarball that contains the PKGBUILD,
so the two can never be self-consistent and the sums always lag one release —
it commits rather than warns because at that point the release is already
public, and forgetting means `yay -S nabria` aborts on a hash mismatch with
nothing on this side ever saying why. Push it with the tag.
