# Packaging

How Nabria reaches a machine, and why in that order.

## The four ways in

| | command | updates arrive |
|---|---|---|
| `.rpm` | `sudo dnf install …/nabria.rpm` | on the next release, once Copr is set up (below) |
| `.deb` | `sudo apt install ./nabria.deb` | by downloading the new one, until there is an apt repo |
| AUR | `yay -S nabria` | with every other AUR package |
| `curl … \| sh` | anything else | by re-running the same line |

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

## Copr — the Fedora update channel

Not set up yet; it needs a Fedora account, which is a person, not a script.
`packaging/nabria.spec` is already suitable as-is: both its sources are URLs,
which is what Copr fetches.

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

## Debian: why there is no apt repository yet

An apt repository needs a signed `InRelease`, which means a GPG private key
living wherever the repository is built. Doing that in GitHub Actions puts a
signing key in CI, which is the dependency this project is deliberately without
— the whole testing story is that everything runs locally and CI only confirms
it. Copr sidesteps signing entirely by doing it itself; Debian has no free
equivalent that does.

So the `.deb` is a release asset for now. The openSUSE Build Service could host
a signed apt repository for Debian and Ubuntu and is the obvious next step if
enough people want one.

## When the version changes

Four files say the version and no build reads them together, so
`tests/test_packaging.py` binds them:

```
src/nabria/__init__.py    __version__     the source of truth
packaging/nabria.spec     Version:
packaging/PKGBUILD        pkgver
the git tag               v<version>      checked by scripts/release.sh
```

`scripts/release.sh` rewrites the PKGBUILD's `sha256sums` after uploading,
because they are the checksums of the tarball it has just published and cannot
be known before. Commit that change; otherwise `yay -S nabria` aborts on a hash
mismatch with nothing on this side saying why.
