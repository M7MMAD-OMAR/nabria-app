#!/bin/bash
# Publishes an application release: the source tarball and the one-line installer.
#
#   scripts/release.sh v0.1.0            build and publish
#   scripts/release.sh v0.1.0 --dry-run  build only, and say what would happen
#
# Separate from release-engine.sh, which publishes the compiled whisper.cpp
# binary. That one happens when engine/VERSION changes, which is rarely; this
# one happens every time the application is tagged.
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

tag=${1:-}
dry_run=no
[ "${2:-}" = --dry-run ] && dry_run=yes
if [ -z "$tag" ]; then
  # The tag on HEAD, when there is one -- so the common case is one argument
  # shorter and cannot disagree with what is checked out.
  tag=$(git describe --tags --exact-match 2>/dev/null || true)
fi
[ -n "$tag" ] || { echo "usage: $0 <tag> [--dry-run]   (no tag on HEAD)" >&2; exit 2; }
git rev-parse -q --verify "refs/tags/$tag" >/dev/null ||
  { echo "no such tag: $tag" >&2; exit 1; }

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tarball=$work/nabria.tar.gz

say "Building $tag"
# From the tag, not the working tree: a release must be reproducible from what
# is committed, and building from the tree would happily ship an uncommitted
# edit that nobody else can ever reproduce.
git archive --format=tar.gz --prefix=nabria/ "$tag" > "$tarball"
ok "nabria.tar.gz — $(du -h "$tarball" | cut -f1)"

# The bootstrap is published beside the tarball rather than only linked out of
# the branch, so the install command names an immutable release asset. Taken
# from the tag too, for the same reason as above.
installer=$work/install-nabria.sh
git show "$tag:scripts/bootstrap.sh" > "$installer"
ok "install-nabria.sh"

say "Checking the tarball installs"
# The one thing that makes this release worthless if wrong. Unpacked exactly
# the way bootstrap.sh unpacks it, and asked for the file bootstrap.sh looks
# for -- catching a rename or a .gitignore that swallowed scripts/ here rather
# than in someone's terminal.
mkdir -p "$work/verify"
tar -xzf "$tarball" -C "$work/verify" --strip-components=1
[ -x "$work/verify/scripts/install.sh" ] || { echo "  no executable scripts/install.sh" >&2; exit 1; }
[ -f "$work/verify/engine/CHECKSUMS" ] || { echo "  no engine/CHECKSUMS — the prebuilt engine could not be verified" >&2; exit 1; }
ok "layout is what bootstrap.sh expects"

if [ "$dry_run" = yes ]; then
  say "Dry run"
  echo "  would publish $tag with nabria.tar.gz and install-nabria.sh"
  exit 0
fi

say "Publishing"
if gh release view "$tag" >/dev/null 2>&1; then
  ok "release $tag exists"
else
  gh release create "$tag" --title "Nabria $tag" --notes-from-tag --latest ||
    gh release create "$tag" --title "Nabria $tag" --generate-notes --latest
  ok "release created"
fi
# --latest explicitly, because releases/latest/download/... is the URL in the
# install command and GitHub decides "latest" by publish date otherwise. An
# engine release published afterwards would quietly take the install command
# with it; engine releases are marked as prereleases for the same reason.
gh release edit "$tag" --latest >/dev/null
gh release upload "$tag" "$tarball" "$installer" --clobber
ok "assets uploaded"

echo
echo "  curl -fsSL https://github.com/M7MMAD-OMAR/nabria-app/releases/latest/download/install-nabria.sh | sh"
