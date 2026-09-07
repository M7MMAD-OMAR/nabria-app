#!/bin/bash
# Run inside MSYS2 UCRT64. Native Windows binaries, no MSYS shell at runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${MSYSTEM:-} != UCRT64 ]]; then
  echo "Run this script in the MSYS2 UCRT64 shell." >&2
  exit 1
fi
version=$(sed -n 's/^WHISPER_CPP_VERSION=//p' engine/VERSION | tr -d '\r')
[[ -n "$version" ]]
mkdir -p build
# main(char **argv) otherwise receives the legacy ANSI code page, corrupting
# Arabic prompts and model paths before whisper can read them.
windres -I packaging/windows packaging/windows/engine.rc -O coff -o build/windows-engine-resource.o
if [[ ! -d build/whisper-windows/.git ]]; then
  git clone --depth 1 --branch "$version" https://github.com/ggml-org/whisper.cpp.git build/whisper-windows
fi
if [[ $(git -C build/whisper-windows describe --tags --exact-match) != "$version" ]]; then
  echo "Engine checkout does not match engine/VERSION" >&2
  exit 1
fi
cmake -S build/whisper-windows -B build/whisper-windows/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF \
  -DGGML_OPENMP=OFF -DGGML_VULKAN=ON -DWHISPER_BUILD_SERVER=ON \
  -DWHISPER_BUILD_TESTS=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="\"$(cygpath -m "$PWD/build/windows-engine-resource.o")\""
cmake --build build/whisper-windows/build --target whisper-server --parallel 2
python scripts/stage-windows.py
gcc -municode -mwindows -O2 packaging/windows/launcher.c -o dist/Nabria/Nabria.exe
