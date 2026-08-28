#!/bin/sh
set -eu

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"

# gtk4-layer-shell has to be loaded before libwayland-client or its Wayland
# protocol hooks never install, and the orb silently becomes an ordinary
# toplevel -- the exact failure this tool was written to avoid. Python has no
# say over link order, so the daemon is preloaded here. Control commands never
# touch GTK, so they skip it and stay fast.
if [ "${1:-}" = "daemon" ]; then
  # shellcheck source=common.sh disable=SC1091
  . "$project_dir/scripts/common.sh"
  layer_shell=$(layer_shell_library) || layer_shell=""
  if [ -n "$layer_shell" ]; then
    LD_PRELOAD="$layer_shell${LD_PRELOAD:+:$LD_PRELOAD}"
    export LD_PRELOAD
  fi
fi

exec python3 -m nabria "$@"
