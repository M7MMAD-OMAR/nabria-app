#!/bin/sh
# The one-line install. Fetches a release and runs its installer.
#
#   curl -fsSL https://github.com/M7MMAD-OMAR/nabria-app/releases/latest/download/install-nabria.sh | sh
#
# Anything after `sh -s --` is passed straight to scripts/install.sh, so the
# install options work through the pipe too:
#
#   curl -fsSL <url> | sh -s -- --model base --no-service
#
# Re-running upgrades in place: the source tree is replaced, and the config,
# models and transcripts live in the XDG directories rather than in it.
#
#   NABRIA_HOME      where the source tree goes
#   NABRIA_TARBALL   install this tarball instead of downloading one
set -eu

REPO=M7MMAD-OMAR/nabria-app
# An asset named without a version, so this URL always resolves to the newest
# release and never has to be edited. It is a release asset rather than a link
# into a branch because a branch rename would break the link silently, and the
# only thing worse than an install command that fails is one that fetches
# something unintended.
DEFAULT_TARBALL="https://github.com/$REPO/releases/latest/download/nabria.tar.gz"

# Everything is inside a function that is called on the last line. `curl | sh`
# feeds the shell as the bytes arrive, so a connection that drops halfway
# through executes half a script; with the body in a function, a truncated
# download is a syntax error that runs nothing.
main() {
  home=${NABRIA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/nabria/app}
  source=${NABRIA_TARBALL:-$DEFAULT_TARBALL}

  # Nabria is a user application: a --user systemd unit, a config under
  # $XDG_CONFIG_HOME, a desktop entry in the user's menu. Under sudo every one
  # of those lands in root's home, and the install appears to succeed while
  # being invisible to the person who ran it.
  if [ "$(id -u)" = 0 ] && [ -z "${NABRIA_ALLOW_ROOT:-}" ]; then
    echo "Run this as yourself, not with sudo -- everything it writes is per-user." >&2
    exit 1
  fi

  command -v tar >/dev/null 2>&1 || { echo "tar is required." >&2; exit 1; }

  # The scratch directory is a sibling of the destination rather than somewhere
  # under /tmp, which is usually a different filesystem -- so the swap below is
  # a rename instead of a copy, and therefore actually atomic.
  mkdir -p "$(dirname "$home")"
  work=$(mktemp -d "$(dirname "$home")/.nabria-install.XXXXXX")
  # Covers the failure paths as well as the happy one: an interrupted download
  # otherwise leaves a temporary tree behind on every retry.
  trap 'rm -rf "$work"' EXIT INT TERM

  case $source in
    http://*|https://*)
      tarball=$work/nabria.tar.gz
      printf '\033[1mFetching\033[0m %s\n' "$source"
      if   command -v curl >/dev/null 2>&1; then curl -fsSL --retry 2 -o "$tarball" "$source"
      elif command -v wget >/dev/null 2>&1; then wget -qO "$tarball" "$source"
      else echo "Neither curl nor wget is installed." >&2; exit 1
      fi || { echo "Download failed." >&2; exit 1; }
      ;;
    *)
      [ -r "$source" ] || { echo "No such tarball: $source" >&2; exit 1; }
      tarball=$source
      ;;
  esac

  # --strip-components=1 rather than trusting the top-level directory's name:
  # a `git archive --prefix` tarball and GitHub's own auto-generated one use
  # different names for it, and both are things people will hand to this.
  mkdir -p "$work/tree"
  tar -xzf "$tarball" -C "$work/tree" --strip-components=1
  [ -x "$work/tree/scripts/install.sh" ] || {
    echo "That tarball does not look like Nabria: no scripts/install.sh in it." >&2
    exit 1
  }

  # The new tree is put in place only once it has been unpacked and checked, so
  # a bad download cannot leave a working install half-replaced.
  rm -rf "$home"
  mv "$work/tree" "$home"
  printf '\033[1mInstalled to\033[0m %s\n' "$home"

  # install.sh writes absolute paths -- the launcher and the systemd unit both
  # point back at this tree -- so it has to run from where the tree now lives,
  # not from the temporary directory it was unpacked in.
  "$home/scripts/install.sh" "$@"
}

main "$@"
