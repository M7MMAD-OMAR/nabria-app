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
