#!/bin/bash
# Publishes an application release: the source tarball and the one-line installer.
#
#   scripts/release.sh v0.2.0            build and publish
#   scripts/release.sh v0.2.0 --dry-run  build only, and say what would happen
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
if [ "$dry_run" = yes ] || gh release view "$ENGINE_RELEASE" >/dev/null 2>&1; then
  ok "engine $ENGINE_RELEASE is published"
else
  bad "engine $ENGINE_RELEASE is not published — run scripts/release-engine.sh first"
  exit 1
fi

if [ "$dry_run" = yes ]; then
  say "Dry run"
  echo "  would publish $tag with nabria.tar.gz and install-nabria.sh"
  exit 0
fi

say "Publishing"
command -v gh >/dev/null 2>&1 || { bad "gh is required to publish"; exit 1; }
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
gh release upload "$tag" "$tarball" "$installer" --clobber
ok "assets uploaded"

echo
echo "  curl -fsSL https://github.com/$NABRIA_REPO/releases/latest/download/install-nabria.sh | sh"
