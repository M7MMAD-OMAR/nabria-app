#!/bin/bash
# Builds the engine as a release artifact and records its checksum.
#
# Deliberately a local script and not a CI job. Publishing an engine happens
# when engine/VERSION changes, which is rarely, and it is better done where the
# result can be looked at than by something that runs unattended and mails a
# red tick.
#
# The build runs in an **old** container on purpose. glibc is forward
# compatible and not backward compatible, so a binary linked against a new one
# simply refuses to start on an older distribution -- and the people who most
# want a prebuilt engine are the least likely to be on the newest release.
#
# Debian bookworm, because it is the oldest image that still *packages* the
# Vulkan shader compiler: Ubuntu 22.04 is a year older but has no `glslc` at
# all, so building there would mean either fetching a compiler from somewhere
# unpinned or shipping a CPU-only engine. bookworm's glibc 2.36 covers Debian
# 12+, Ubuntu 22.10+ and Fedora 37+. Anything older falls back to building from
# source, which install.sh does automatically when the download will not run.
#
#   scripts/release-engine.sh            build and record the checksum
#   scripts/release-engine.sh --publish  also upload it to a GitHub release
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir" || exit 1
# shellcheck source=common.sh disable=SC1091
. "$project_dir/scripts/common.sh"
# shellcheck source=../engine/VERSION disable=SC1091
source engine/VERSION

BUILDER_IMAGE=docker.io/library/debian:bookworm
ARTIFACT=whisper-server-linux-x86_64
publish=no
[ "${1:-}" = "--publish" ] && publish=yes

runner=$(container_runner) ||
  { echo "podman or docker is required" >&2; exit 1; }

echo "Building $ARTIFACT from whisper.cpp $WHISPER_CPP_VERSION in $BUILDER_IMAGE"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The container script is single quoted on purpose: it runs in there, so
# nothing in it should expand against this shell.
# shellcheck disable=SC2016
$runner run --rm -v "$project_dir":/src:ro,z -v "$work":/out:z \
  -e "VULKAN_HEADERS_VERSION=$VULKAN_HEADERS_VERSION" "$BUILDER_IMAGE" \
  bash -c '
    set -e
    apt-get update -qq >/dev/null
    apt-get install -y -qq git cmake build-essential glslc libvulkan-dev \
                          spirv-headers >/dev/null

    # Newer Vulkan headers over the packaged ones. Header-only and installed
    # to /usr/local, which cmake searches first.
    git clone --depth 1 --branch "$VULKAN_HEADERS_VERSION" \
      https://github.com/KhronosGroup/Vulkan-Headers.git /tmp/vulkan-headers 2>&1 | tail -1
    cmake -S /tmp/vulkan-headers -B /tmp/vulkan-headers/build \
      -DCMAKE_INSTALL_PREFIX=/usr/local >/dev/null
    cmake --install /tmp/vulkan-headers/build >/dev/null
    echo "vulkan headers: $(grep -m1 VK_HEADER_VERSION /usr/local/include/vulkan/vulkan_core.h)"
    cp -r /src /app
    /app/scripts/build-engine.sh --portable --output /out/whisper-server >/dev/null
    # Recorded in the log so a "will not start" report can be answered without
    # guessing what it was built against.
    echo "glibc required: $(objdump -T /out/whisper-server \
      | grep -oE "GLIBC_[0-9]+\.[0-9]+" | sort -Vu | tail -1)"
  '

mv "$work/whisper-server" "$ARTIFACT"
chmod +x "$ARTIFACT"
sum=$(sha256sum "$ARTIFACT" | cut -d' ' -f1)

# The checksum lives in the repository, not only beside the download. Users
# clone this, so an in-repo checksum is something they already trust; verifying
# a download against a hash published next to that same download would check
# only that the bytes arrived intact, not that they are the bytes we built.
printf '%s  %s\n' "$sum" "$ARTIFACT" > engine/CHECKSUMS
echo
echo "  $ARTIFACT  $(du -h "$ARTIFACT" | cut -f1)"
echo "  sha256 $sum"
echo "  recorded in engine/CHECKSUMS"

if [ "$publish" = yes ]; then
  command -v gh >/dev/null 2>&1 || { echo "gh is required to publish" >&2; exit 1; }
  echo
  echo "Publishing as $ENGINE_RELEASE"
  # --prerelease is load-bearing, not a label. The install command in the
  # README and bootstrap.sh's default both resolve through
  # releases/latest/download/, and GitHub picks "latest" by publish date --
  # so without this the next engine build silently repoints the one-line
  # install at a release whose only asset is a whisper-server binary, and it
  # 404s. A prerelease is never latest.
  gh release view "$ENGINE_RELEASE" >/dev/null 2>&1 ||
    gh release create "$ENGINE_RELEASE" \
      --title "Engine $ENGINE_RELEASE" \
      --prerelease \
      --notes "whisper-server built from whisper.cpp $WHISPER_CPP_VERSION.
Static, Vulkan compiled in with automatic CPU fallback, built on Ubuntu 22.04
so it runs on that vintage of glibc and newer.

Fetched by \`scripts/install.sh\`, which verifies it against
\`engine/CHECKSUMS\` in the repository rather than against anything here."
  gh release upload "$ENGINE_RELEASE" "$ARTIFACT" --clobber
  echo "  uploaded"
fi

echo
echo "Commit engine/CHECKSUMS so installs verify against it."
