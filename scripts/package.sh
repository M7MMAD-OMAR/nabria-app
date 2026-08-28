#!/bin/bash
# Builds the distribution packages.
#
#   scripts/package.sh            both, into dist/
#   scripts/package.sh --rpm
#   scripts/package.sh --deb
#
# Each is built inside a container of the distribution it targets, so the
# result is what that distribution's own tools produce rather than what this
# machine's happen to. Nothing is installed on the host and neither rpmbuild
# nor dpkg-deb needs to exist here.
#
# The engine is bundled, so `dnf install nabria` leaves nothing to compile and
# nothing to fetch but the model. That makes the packages x86_64 -- which the
# engine already was.
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
# shellcheck source=common.sh disable=SC1091
. "$project_dir/scripts/common.sh"
# shellcheck source=../engine/VERSION disable=SC1091
source "$project_dir/engine/VERSION"

want_rpm=yes want_deb=yes
case ${1:-} in
  --rpm) want_deb=no ;;
  --deb) want_rpm=no ;;
  "")    ;;
  *) echo "usage: $0 [--rpm|--deb]" >&2; exit 2 ;;
esac

runner=$(container_runner) || { bad "neither podman nor docker"; exit 1; }
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/nabria/__init__.py)
[ -n "$version" ] || { bad "no __version__ in src/nabria/__init__.py"; exit 1; }

# Named without the version, so the install lines in the README can point at
# releases/latest/download/nabria.rpm and never need editing again. The version
# is inside the package, which is where dnf and apt read it from anyway.
dist=$project_dir/dist
rm -rf "$dist"; mkdir -p "$dist"

# --------------------------------------------------------------------- engine

say "Engine"
engine=$dist/whisper-server
artifact=$(awk 'NR==1 {print $2}' engine/CHECKSUMS)
expected=$(awk 'NR==1 {print $1}' engine/CHECKSUMS)
if [ -x "$project_dir/$artifact" ] &&
   [ "$(sha256sum "$project_dir/$artifact" | cut -d\  -f1)" = "$expected" ]; then
  cp "$project_dir/$artifact" "$engine"
  ok "using the local build"
else
  curl -fsSL --retry 2 -o "$engine" \
    "https://github.com/$NABRIA_REPO/releases/download/$ENGINE_RELEASE/$artifact"
  ok "fetched $ENGINE_RELEASE"
fi
# Verified against the checksum committed here, exactly as install.sh does.
# Shipping an unverified binary inside a package is worse than shipping none:
# a package is the one artifact a user has no reason to check themselves.
[ "$(sha256sum "$engine" | cut -d\  -f1)" = "$expected" ] ||
  { bad "engine checksum mismatch — refusing to package it"; exit 1; }
chmod 755 "$engine"
ok "checksum verified"

# The source tree as it would ship, so the packages contain what the release
# contains and not what happens to be lying around the working directory.
release_tarball HEAD > "$dist/nabria.tar.gz"

# ------------------------------------------------------------------------ rpm

if [ "$want_rpm" = yes ]; then
  say "RPM"
  log=$(mktemp)
  if $runner run --rm -v "$dist":/dist:z docker.io/library/fedora:44 bash -c "
        set -e
        dnf install -y -q rpm-build systemd-rpm-macros >/dev/null
        mkdir -p /root/rpmbuild/SOURCES
        cp /dist/nabria.tar.gz /root/rpmbuild/SOURCES/
        cp /dist/whisper-server /root/rpmbuild/SOURCES/$artifact
        mkdir -p /tmp/tree && tar -xzf /dist/nabria.tar.gz -C /tmp/tree --strip-components=1
        rpmbuild -bb /tmp/tree/packaging/nabria.spec
        cp /root/rpmbuild/RPMS/x86_64/*.rpm /dist/nabria.rpm
      " >"$log" 2>&1
  then
    ok "nabria.rpm"
  else
    sed 's/^/    /' "$log"; bad "rpm build"; exit 1
  fi
  rm -f "$log"
fi

# ------------------------------------------------------------------------ deb

if [ "$want_deb" = yes ]; then
  say "DEB"
  log=$(mktemp)
  # A binary package built with dpkg-deb from a staged tree, not a Debian
  # source package. Honest about what it is: it is a release asset people
  # download and `apt install ./nabria.deb`, and a source package would only
  # be needed to run a PPA -- which needs a signing key in CI, which is the
  # dependency this project is deliberately avoiding.
  if $runner run --rm -v "$dist":/dist:z docker.io/library/debian:trixie bash -c "
        set -e
        apt-get update -qq && apt-get install -y -qq dpkg-dev >/dev/null 2>&1
        mkdir -p /tmp/tree && tar -xzf /dist/nabria.tar.gz -C /tmp/tree --strip-components=1
        cd /tmp/tree
        . packaging/layout.sh
        stage_nabria /tmp/pkg . /dist/whisper-server
        install -Dpm 644 LICENSE /tmp/pkg/usr/share/doc/nabria/copyright
        install -d /tmp/pkg/DEBIAN
        sed 's/@VERSION@/$version/' packaging/debian-control > /tmp/pkg/DEBIAN/control
        dpkg-deb --root-owner-group --build /tmp/pkg /dist/nabria.deb
      " >"$log" 2>&1
  then
    ok "nabria.deb"
  else
    sed 's/^/    /' "$log"; bad "deb build"; exit 1
  fi
  rm -f "$log"
fi

# ---------------------------------------------------------------------- tidy

# The staging inputs are not artifacts; leaving them makes it ambiguous which
# files in dist/ are meant to be uploaded.
rm -f "$dist/whisper-server" "$dist/nabria.tar.gz"

say "Built"
find "$dist" -type f -printf '  %f  %s bytes\n' | sort
