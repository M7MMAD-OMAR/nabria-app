# shellcheck shell=sh
# Shared by run.sh, install.sh, check.sh and release-engine.sh.
#
# POSIX, not bash: run.sh is #!/bin/sh and is the one that matters most --
# it runs on every keypress path.
#
# It lives directly in scripts/ rather than scripts/lib/ on purpose. check.sh
# lints `scripts/*.sh`, and that glob does not descend into a directory, so a
# helper tucked away in one would silently escape shellcheck -- which is the
# same class of invisible drift this file exists to end.

# Written out in install.sh and again in the URL release.sh prints. Renaming
# the account or the repository would leave one of them printing a 404 as its
# last line, at the moment nobody re-reads it. bootstrap.sh keeps its own copy
# and must: it ships as a standalone release asset with nothing to source.
# shellcheck disable=SC2034  # used by the scripts that source this
NABRIA_REPO=M7MMAD-OMAR/nabria-app

# The install/release voice. install.sh and release.sh had byte-identical
# copies of the first two and release.sh had none of the last two, so its
# failures fell back to a bare echo with a hand-typed indent imitating this
# alignment. check.sh deliberately keeps its own step/pass/fail: it is
# reporting verdicts and counting them, not narrating an install.
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# The release tarball, exactly as published.
#
# `git archive`, not a tar of the working tree: the working tree carries
# untracked files, __pycache__ and a gitignored 39 MB engine binary, so a tar
# of it is the one archive that cannot catch the thing this is used to
# check -- a file that never got committed is present in it and absent from
# the release. Both release.sh and check.sh call this, so the archive under
# test is the archive that ships, by construction rather than by comment.
release_tarball() {
  git archive --format=tar.gz --prefix=nabria/ "${1:-HEAD}"
}

# Where gtk4-layer-shell is, or "" if it is not installed.
#
# This was written out separately in three scripts and the three had already
# disagreed: run.sh knew about Debian's multiarch directory and /usr/local/lib,
# the other two did not. The consequences were both silent. install.sh reported
# the library missing on machines that had it, and check.sh ran the test suite
# without the LD_PRELOAD, so the layered code path -- the one the daemon
# actually uses -- skipped itself and the suite still went green.
layer_shell_library() {
  for candidate in \
    /usr/lib64/libgtk4-layer-shell.so.0 \
    /usr/lib/libgtk4-layer-shell.so.0 \
    /usr/lib/*/libgtk4-layer-shell.so.0 \
    /usr/local/lib/libgtk4-layer-shell.so.0 \
    /usr/local/lib64/libgtk4-layer-shell.so.0
  do
    if [ -e "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# podman, docker, or "" when neither is installed.
container_runner() {
  for candidate in podman docker; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}
