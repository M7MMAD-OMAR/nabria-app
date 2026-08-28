#!/bin/bash
# Publishes an application release: the source tarball and the one-line installer.
#
#   scripts/release.sh v0.3.0            build and publish
#   scripts/release.sh v0.3.0 --dry-run  build only, and say what would happen
#
# Separate from release-engine.sh, which publishes the compiled whisper.cpp
# binary. That one happens when engine/VERSION changes, which is rarely; this
# one happens every time the application is tagged.
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
# shellcheck source=common.sh disable=SC1091
. "$project_dir/scripts/common.sh"

tag=${1:-}
[ -n "$tag" ] || { echo "usage: $0 <tag> [--dry-run]" >&2; exit 2; }
dry_run=no
[ "${2:-}" = --dry-run ] && dry_run=yes

git rev-parse -q --verify "refs/tags/$tag" >/dev/null ||
  { bad "no such tag: $tag"; exit 1; }

# The tag has to agree with the version in the source, so a release cannot be
# published under a name the repository does not claim -- the discipline
# engine/VERSION already gives the engine. Read from the tag rather than from
# the working tree, since the tag is what gets archived below.
version=$(git show "$tag:src/nabria/__init__.py" | sed -n 's/^__version__ = "\(.*\)"/\1/p')
[ "$tag" = "v$version" ] ||
  { bad "$tag is tagged on a commit whose __version__ is $version"; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tarball=$work/nabria.tar.gz

say "Building $tag"
# From the tag, not the working tree: a release must be reproducible from what
# is committed, and building from the tree would happily ship an uncommitted
# edit that nobody else can ever reproduce. release_tarball is shared with
# check.sh, which installs from it -- so the archive that is tested and the
# archive that ships are the same one by construction.
release_tarball "$tag" > "$tarball"
ok "nabria.tar.gz — $(du -h "$tarball" | cut -f1)"

# The bootstrap is published beside the tarball rather than only linked out of
# the branch, so the install command names an immutable release asset. Taken
# from the tag too, for the same reason as above.
installer=$work/install-nabria.sh
git show "$tag:scripts/bootstrap.sh" > "$installer"
ok "install-nabria.sh"

say "Checking what it will install"
# Unpacked the way bootstrap.sh unpacks it, and asked for the file bootstrap.sh
# looks for -- catching a rename, or a .gitignore that swallowed scripts/, here
# rather than in someone's terminal.
mkdir -p "$work/verify"
tar -xzf "$tarball" -C "$work/verify" --strip-components=1
[ -x "$work/verify/scripts/install.sh" ] || { bad "no executable scripts/install.sh"; exit 1; }
ok "layout is what bootstrap.sh expects"

# And that the engine it will try to fetch has actually been published. The
# checksum being committed proves only that someone built the binary; if the
# matching release was never pushed, every install falls through to a
# five-minute source compile with "download failed" as the whole explanation.
# shellcheck source=../engine/VERSION disable=SC1091
source "$work/verify/engine/VERSION"
if ! command -v gh >/dev/null 2>&1; then
  # Asked before the check that uses it, and reported as itself. A missing gh
  # makes `gh release view` fail exactly like an unpublished engine does, and
  # the message for that sends you to a script which would also fail on gh --
  # for a release that is in fact published.
  [ "$dry_run" = yes ] || { bad "gh is required to publish"; exit 1; }
  warn "gh is not installed, so whether $ENGINE_RELEASE is published is unknown"
elif gh release view "$ENGINE_RELEASE" >/dev/null 2>&1; then
  ok "engine $ENGINE_RELEASE is published"
else
  bad "engine $ENGINE_RELEASE is not published — run scripts/release-engine.sh first"
  exit 1
fi

# The packages are built by scripts/package.sh, deliberately as a separate step:
# it runs two containers and takes minutes, and a release should not silently
# rebuild them. But publishing without them would leave the README's dnf and
# apt lines pointing at assets that are not there.
for package in nabria.rpm nabria.deb; do
  [ -f "$project_dir/dist/$package" ] ||
    { bad "dist/$package is missing — run scripts/package.sh first"; exit 1; }
done
ok "packages present"

if [ "$dry_run" = yes ]; then
  say "Dry run"
  echo "  would publish $tag with the tarball, the installer, nabria.rpm and nabria.deb"
  exit 0
fi

say "Publishing"
if gh release view "$tag" >/dev/null 2>&1; then
  ok "release $tag exists"
else
  gh release create "$tag" --title "Nabria $tag" --notes-from-tag ||
    gh release create "$tag" --title "Nabria $tag" --generate-notes
  ok "release created"
fi
# Unconditionally, and after the create rather than as a flag on it, so the
# same line covers both branches: releases/latest/download/... is the URL in
# the install command, and GitHub decides "latest" by publish date otherwise.
gh release edit "$tag" --latest >/dev/null
gh release upload "$tag" "$tarball" "$installer" "$project_dir"/dist/nabria.rpm \
  "$project_dir"/dist/nabria.deb --clobber
ok "assets uploaded"

# The AUR PKGBUILD names the checksum of the tarball it downloads, which cannot
# be known until that tarball is published -- so its sums are always one release
# behind until this runs. Rewritten here rather than left to be remembered,
# because the failure is a `yay -S nabria` that aborts on a hash mismatch and
# nothing on this side that ever says so.
# The PKGBUILD names the checksum of the tarball it downloads, and that tarball
# contains the PKGBUILD -- so the two can never be self-consistent and the sums
# are always one release behind. Rewritten *and committed* here rather than
# left to a warning: the release is already public at this point, and the
# failure of forgetting is a `yay -S nabria` that aborts on a hash mismatch
# with nothing on this side ever saying so.
tarball_sum=$(sha256sum "$tarball" | cut -d\  -f1)
engine_sum=$(awk 'NR==1 {print $1}' "$project_dir/engine/CHECKSUMS")
sed -i "s/^sha256sums=('[0-9a-f]*'/sha256sums=('$tarball_sum'/
        s/^            '[0-9a-f]*')/            '$engine_sum')/" \
  "$project_dir/packaging/PKGBUILD"
if git diff --quiet -- "$project_dir/packaging/PKGBUILD"; then
  ok "PKGBUILD checksums already correct"
else
  git commit -q -m "Update the PKGBUILD checksums for $tag" -- "$project_dir/packaging/PKGBUILD"
  ok "PKGBUILD checksums updated and committed — push when you are ready"
fi

echo
echo "  sudo dnf install https://github.com/$NABRIA_REPO/releases/latest/download/nabria.rpm"
echo "  curl -fsSL https://github.com/$NABRIA_REPO/releases/latest/download/install-nabria.sh | sh"
