#!/bin/sh
# The launcher, for both layouts it can be installed in.
#
#   a checkout          <project>/scripts/run.sh  with the code in <project>/src
#   a package           /usr/bin/nabria           with the code in /usr/lib/nabria
#
# One script rather than two, because the LD_PRELOAD below is the only thing
# standing between a working indicator and a silently degraded one -- and a
# second launcher written for packages is exactly where that gets left out.
set -eu

prefix=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

if [ -d "$prefix/src/nabria" ]; then
  code=$prefix/src
  helpers=$prefix/scripts/common.sh
else
  # The helper is packaged beside the code rather than inlined here, so
  # layer_shell_library still has exactly one definition -- it was written out
  # three times once before, and the three had already disagreed.
  code=$prefix/lib/nabria
  helpers=$prefix/lib/nabria/common.sh
fi
export PYTHONPATH="$code${PYTHONPATH:+:$PYTHONPATH}"

# gtk4-layer-shell has to be loaded before libwayland-client or its Wayland
# protocol hooks never install, and the orb silently becomes an ordinary
# toplevel -- the exact failure this tool was written to avoid. Python has no
# say over link order, so the daemon is preloaded here. Control commands never
# touch GTK, so they skip it and stay fast.
# No `[ -r "$helpers" ]` guard: a missing common.sh must be a hard failure
# under set -eu, not a quiet skip. Skipping it would start the daemon with no
# LD_PRELOAD, which is the silently-degraded indicator this whole arrangement
# exists to prevent -- the same failure, reintroduced as a fallback.
if [ "${1:-}" = "daemon" ]; then
  # shellcheck source=common.sh disable=SC1091
  . "$helpers"
  layer_shell=$(layer_shell_library) || layer_shell=""
  if [ -n "$layer_shell" ]; then
    LD_PRELOAD="$layer_shell${LD_PRELOAD:+:$LD_PRELOAD}"
    export LD_PRELOAD
  fi
fi

exec python3 -m nabria "$@"
