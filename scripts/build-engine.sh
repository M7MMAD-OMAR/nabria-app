#!/bin/bash
# Builds whisper-server from upstream whisper.cpp.
#
# The result is deliberately one self-contained file. Static linking plus
# GGML_NATIVE=OFF means the same binary runs on any x86-64 machine, and ggml
# still selects AVX2/FMA paths at runtime -- measured no slower than a
# -march=native build (21.4s against 21.9s on the same audio), so portability
# costs nothing here.
#
# Vulkan is compiled in when the shader toolchain is present. That is not a
# hard requirement: a Vulkan-enabled binary falls back to the CPU by itself
# where no driver exists, so one artifact serves every machine.
#
# --portable links the GCC runtimes in as well, so the binary also runs on
# machines with an older libstdc++ than the one that built it. That needs a
# static libstdc++ installed, which an ordinary machine does not have, so it is
# only for the published artifact -- see scripts/release-engine.sh.
#
#   scripts/build-engine.sh [--output PATH] [--no-vulkan] [--keep-source] [--portable]
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck source=../engine/VERSION disable=SC1091
source "$project_dir/engine/VERSION"

output=$HOME/.local/libexec/nabria/whisper-server
want_vulkan=yes
keep_source=no
want_portable=no
source_dir=${NABRIA_BUILD_DIR:-${TMPDIR:-/tmp}/nabria-engine-build}

while [ $# -gt 0 ]; do
  case $1 in
    --output) output=$2; shift 2 ;;
    --no-vulkan) want_vulkan=no; shift ;;
    --keep-source) keep_source=yes; shift ;;
    --portable) want_portable=yes; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

missing=""
for tool in git cmake make cc c++; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
  echo "missing build tools:$missing" >&2
  echo "  Fedora: sudo dnf install git cmake gcc gcc-c++" >&2
  echo "  Debian: sudo apt install git cmake build-essential" >&2
  echo "  Arch:   sudo pacman -S git cmake base-devel" >&2
  exit 1
fi

# Vulkan needs a shader compiler and the SPIR-V headers at build time. Checked
# rather than assumed, because the cmake failure when they are absent names
# SPIRV-Headers and gives no hint that a package is missing.
if [ "$want_vulkan" = yes ]; then
  if ! command -v glslc >/dev/null 2>&1 || [ ! -e /usr/include/vulkan/vulkan.h ]; then
    echo "note: no Vulkan shader toolchain, building a CPU-only engine."
    echo "      For GPU support install:"
    echo "        Fedora: sudo dnf install glslc vulkan-headers vulkan-loader-devel spirv-headers-devel"
    echo "        Debian: sudo apt install glslc libvulkan-dev spirv-headers"
    echo "        Arch:   sudo pacman -S shaderc vulkan-headers"
    want_vulkan=no
  fi
fi

vulkan_flag=OFF
[ "$want_vulkan" = yes ] && vulkan_flag=ON

echo "whisper.cpp $WHISPER_CPP_VERSION -> $output"
rm -rf "$source_dir"
git clone --depth 1 --branch "$WHISPER_CPP_VERSION" \
  https://github.com/ggml-org/whisper.cpp.git "$source_dir" 2>&1 | tail -1

# OpenMP off, so the result does not need libgomp.so.1 -- which is part of the
# compiler runtime and simply absent on a machine that has never built
# anything. Measured on a minimal Debian 12, a binary linking it would not
# start at all, and a binary that fails to *load* is worse than a slow one.
# ggml has its own threadpool; OpenMP is a convenience, not a requirement, and
# CPU inference measured faster without it.
#
# The static GCC runtimes are a different matter and are opt-in. They exist so
# the *published* binary runs on machines older than the one that built it, and
# they need libstdc++-static / libstdc++-*-dev, which is not installed on an
# ordinary developer machine -- so requiring them unconditionally broke the
# build-from-source fallback for everyone whose architecture has no prebuilt.
# That is precisely the person who has no other option.
linker_flags=""
[ "$want_portable" = yes ] && linker_flags="-static-libgcc -static-libstdc++"

cmake -S "$source_dir" -B "$source_dir/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWHISPER_BUILD_SERVER=ON \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="$linker_flags" \
  -DGGML_VULKAN="$vulkan_flag" \
  > "$source_dir/configure.log" 2>&1 || {
    echo "cmake configure failed:" >&2; tail -20 "$source_dir/configure.log" >&2; exit 1;
  }

cmake --build "$source_dir/build" --target whisper-server \
  -j "$(nproc 2>/dev/null || echo 4)" \
  > "$source_dir/build.log" 2>&1 || {
    echo "build failed:" >&2; tail -20 "$source_dir/build.log" >&2; exit 1;
  }

mkdir -p "$(dirname "$output")"
install -m 755 "$source_dir/build/bin/whisper-server" "$output"

# The licence travels with the binary: whisper.cpp is MIT, and redistributing
# it without the notice is exactly the mistake this build replaced.
install -m 644 "$source_dir/LICENSE" "$(dirname "$output")/whisper.cpp-LICENSE"

[ "$keep_source" = yes ] || rm -rf "$source_dir"

echo "built $(du -h "$output" | cut -f1) with vulkan=$want_vulkan"
if "$output" --help >/dev/null 2>&1; then
  echo "engine runs"
else
  echo "the engine was built but will not run" >&2
  exit 1
fi
