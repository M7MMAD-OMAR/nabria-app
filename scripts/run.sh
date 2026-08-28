#!/bin/sh
set -eu

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"

# gtk4-layer-shell has to be loaded before libwayland-client or its Wayland
# protocol hooks never install, and the orb silently becomes an ordinary
# toplevel -- the exact failure this tool was written to avoid. Python has no
# say over link order, so the daemon is preloaded here. Control commands never
# touch GTK, so they skip it and stay fast.
#
# The multiarch path matters: Debian and Ubuntu put the library in
# /usr/lib/x86_64-linux-gnu, which the first two candidates miss entirely. The
# result there was silent -- the daemon started, the indicator became an
# ordinary window, and nothing said why.
if [ "${1:-}" = "daemon" ]; then
  for candidate in \
    /usr/lib64/libgtk4-layer-shell.so.0 \
    /usr/lib/libgtk4-layer-shell.so.0 \
    /usr/lib/*-linux-gnu*/libgtk4-layer-shell.so.0 \
    /usr/local/lib/libgtk4-layer-shell.so.0
  do
    if [ -e "$candidate" ]; then
      LD_PRELOAD="$candidate${LD_PRELOAD:+:$LD_PRELOAD}"
      export LD_PRELOAD
      break
    fi
  done
fi

exec python3 -m nabria "$@"
