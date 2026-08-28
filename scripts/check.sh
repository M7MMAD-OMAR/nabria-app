#!/bin/bash
# Everything CI runs, runnable here.
#
# This is the primary check, not a convenience wrapper around one. CI calls
# this same script, so the two cannot drift into testing different things --
# and more importantly, nothing here needs GitHub: a machine with podman can
# verify every distribution offline.
#
#   scripts/check.sh              lint + tests + every distribution
#   scripts/check.sh --quick      lint + tests only (a few seconds)
#   scripts/check.sh --distros    the distribution matrix only
#   scripts/check.sh --engine     build the engine and transcribe for real
set -uo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir" || exit 1
# shellcheck source=common.sh disable=SC1091
. "$project_dir/scripts/common.sh"

want_distros=yes want_tests=yes want_engine=no
case ${1:-} in
  --quick)   want_distros=no ;;
  --distros) want_tests=no ;;
  --engine)  want_distros=no want_tests=no want_engine=yes ;;
  --all)     want_engine=yes ;;
  "")        ;;
  *) echo "usage: $0 [--quick|--distros|--engine|--all]" >&2; exit 2 ;;
esac

failures=0
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; failures=$((failures + 1)); }

# -------------------------------------------------------------------- lint

if [ "$want_tests" = yes ]; then
  step "Shell"
  if command -v shellcheck >/dev/null 2>&1; then
    # Severity stated rather than inherited: versions differ between machines
    # and CI, and a check that means something different in two places is not
    # a check.
    if shellcheck -S info scripts/*.sh; then pass "shellcheck"; else fail "shellcheck"; fi
  else
    echo "  (shellcheck not installed, skipped)"
  fi

  step "Python"
  if python3 -m compileall -q src tests > /dev/null; then
    pass "everything parses"
  else
    fail "syntax"
  fi

  step "Tests"
  # LD_PRELOAD so the layer-shell path is exercised rather than skipped -- it
  # is the one the daemon actually runs, and testing only the fallback would
  # leave the real code path unmeasured.
  preload=$(layer_shell_library) || preload=""
  if LD_PRELOAD="$preload" python3 -m pytest -q; then
    pass "test suite"
  else
    fail "test suite"
  fi
fi

# ------------------------------------------------------------------ engine

if [ "$want_engine" = yes ]; then
  step "Engine build and a real transcription"
  out=$(mktemp -d)/whisper-server
  if ./scripts/build-engine.sh --output "$out" > /dev/null 2>&1; then
    pass "built"
    if PYTHONPATH=src python3 - "$out" <<'PY'
import pathlib, struct, sys, wave
from nabria import config, whisper
wav = pathlib.Path("/tmp/nabria-check.wav")
with wave.open(str(wav), "wb") as sink:
    sink.setnchannels(1); sink.setsampwidth(2); sink.setframerate(16000)
    sink.writeframes(struct.pack("<16000h", *([0] * 16000)))
models = config.models()
if not models:
    print("  (no model installed, transcription skipped)"); raise SystemExit(0)
settings = {**config.load(), "server_binary": sys.argv[1],
            "model": str(min(models, key=lambda p: p.stat().st_size))}
server = whisper.WhisperServer(settings, lambda m: None)
server.transcribe(wav); server.stop()
PY
    then pass "the built engine transcribes"; else fail "the built engine did not answer"; fi
  else
    fail "engine build"
  fi
fi

# --------------------------------------------------------------- distributions

# The installer's whole job is to be right about a system it has never seen,
# and every bug found in it so far was found this way rather than by reading:
# a library path Debian uses and Fedora does not, a typelib packaged
# separately, pw-record living somewhere else on Arch. Reasoning about package
# names does not work.
DISTROS=(
  "ubuntu:24.04|apt-get update -qq && apt-get install -y -qq python3 python3-gi gir1.2-gtk-4.0 python3-cairo python3-gi-cairo pipewire-bin wl-clipboard wtype python3-pytest"
  "debian:trixie|apt-get update -qq && apt-get install -y -qq python3 python3-gi gir1.2-gtk-4.0 python3-cairo python3-gi-cairo libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 pipewire-bin wl-clipboard wtype python3-pytest"
  "fedora:44|dnf install -y -q python3-gobject python3-cairo gtk4 gtk4-layer-shell pipewire-utils wl-clipboard wtype python3-pytest"
)

if [ "$want_distros" = yes ]; then
  runner=$(container_runner) || runner=""
  if [ -z "$runner" ]; then
    step "Distributions"
    echo "  (neither podman nor docker, skipped)"
  else
    for entry in "${DISTROS[@]}"; do
      image=${entry%%|*}
      setup=${entry#*|}
      step "Distribution: $image"
      log=$(mktemp)
      if $runner run --rm -v "$project_dir":/src:ro,z "docker.io/library/$image" \
           bash -c "set -e
             $setup >/dev/null 2>&1
             cp -r /src /app && cd /app
             # The dependency check must pass with exactly the packages this
             # distribution's own hint names -- that is the thing being tested.
             ./scripts/install.sh --no-engine --no-model --no-service
             # And the tests must pass on its Python. No file list: every
             # module that needs a display, GTK or an engine skips itself, and
             # a hand-written list only goes stale -- test_portal.py was added
             # to the suite and to neither copy of the list that used to be
             # here, so it ran in no container at all.
             python3 -m pytest -q -p no:cacheprovider
           " >"$log" 2>&1
      then
        pass "$image — $(tail -1 "$log")"
      else
        # The whole log, not a tail: this is the only place a packaging bug
        # shows itself, and truncating the one output that matters is how a
        # failure becomes a mystery.
        sed 's/^/    /' "$log"
        fail "$image"
      fi
      rm -f "$log"
    done
  fi
fi

# ------------------------------------------------------------------- verdict

echo
if [ "$failures" -eq 0 ]; then
  printf '\033[32mall checks passed\033[0m\n'
else
  printf '\033[31m%d check(s) failed\033[0m\n' "$failures"
fi
exit $((failures > 0))
