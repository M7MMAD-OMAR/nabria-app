# shellcheck shell=sh
# Where a packaged Nabria's files go. One definition, used by every format.
#
# The .spec calls this from %install and the .deb build calls it to stage its
# tree, so an rpm and a deb cannot end up with different layouts -- which would
# otherwise be found by a user, on one of the two.
#
#   stage_nabria <destdir> <source tree> [engine binary]
#
# The engine is optional: a package can bundle it, or leave it to be fetched on
# first run.

APP_ID=com.sbarah.Nabria
UNIT_NAME=app-$APP_ID.service

stage_nabria() {
  destdir=$1
  source_tree=$2
  engine=${3:-}

  # /usr/lib/nabria rather than a Python site-packages directory: Fedora and
  # Debian disagree about that path (`.../python3.13/site-packages` against
  # `python3/dist-packages`) and about which Python minor version is in it, so
  # naming either one hardcodes a distribution. run.sh puts this on PYTHONPATH,
  # exactly as it does with src/ in a checkout.
  install -d "$destdir/usr/lib/nabria/nabria"
  install -m 644 "$source_tree"/src/nabria/*.py "$destdir/usr/lib/nabria/nabria/"

  # Beside the code, because run.sh sources it for layer_shell_library --
  # the lookup that decides whether the indicator floats above other windows
  # or is covered by them.
  install -m 644 "$source_tree/scripts/common.sh" "$destdir/usr/lib/nabria/common.sh"

  install -d "$destdir/usr/bin"
  install -m 755 "$source_tree/scripts/run.sh" "$destdir/usr/bin/nabria"

  if [ -n "$engine" ]; then
    install -d "$destdir/usr/libexec/nabria"
    install -m 755 "$engine" "$destdir/usr/libexec/nabria/whisper-server"
  fi

  # The unit name is load-bearing: xdg-desktop-portal identifies a host
  # application by it, so app-<app-id>.service is what makes the global
  # shortcuts bind at all. nabria.service stays as a symlink because every
  # instruction ever written says `systemctl --user restart nabria`.
  install -d "$destdir/usr/lib/systemd/user"
  sed 's#@RUN_SH@#/usr/bin/nabria#g' "$source_tree/systemd/$UNIT_NAME" \
    > "$destdir/usr/lib/systemd/user/$UNIT_NAME"
  chmod 644 "$destdir/usr/lib/systemd/user/$UNIT_NAME"
  ln -sf "$UNIT_NAME" "$destdir/usr/lib/systemd/user/nabria.service"

  install -d "$destdir/usr/share/applications"
  install -m 644 "$source_tree/share/$APP_ID.desktop" \
    "$destdir/usr/share/applications/$APP_ID.desktop"
}
