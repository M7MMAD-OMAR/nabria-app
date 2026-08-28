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
#   scripts/build-engine.sh [--output PATH] [--no-vulkan] [--keep-source]
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck source=../engine/VERSION disable=SC1091
source "$project_dir/engine/VERSION"

output=$HOME/.local/libexec/nabria/whisper-server
want_vulkan=yes
keep_source=no
source_dir=${NABRIA_BUILD_DIR:-${TMPDIR:-/tmp}/nabria-engine-build}

while [ $# -gt 0 ]; do
  case $1 in
    --output) output=$2; shift 2 ;;
    --no-vulkan) want_vulkan=no; shift ;;
    --keep-source) keep_source=yes; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
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

# OpenMP off and the GCC runtimes linked in, so the result needs nothing but
# libc and -- when Vulkan is compiled in -- the Vulkan loader. A binary that
# fails to *load* is worse than a slow one: measured on a minimal Debian 12 it
# would not start at all, because libgomp.so.1 is part of the compiler runtime
# and is simply not installed on a machine that has never built anything.
# ggml has its own threadpool; OpenMP is a convenience, not a requirement.
cmake -S "$source_dir" -B "$source_dir/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWHISPER_BUILD_SERVER=ON \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" \
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
