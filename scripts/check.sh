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
#   scripts/check.sh --packages   install the built .rpm and .deb in containers
#   scripts/check.sh --engine     build the engine and transcribe for real
#   scripts/check.sh --all        every one of the above
set -uo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir" || exit 1
# shellcheck source=common.sh disable=SC1091
. "$project_dir/scripts/common.sh"

want_distros=yes want_tests=yes want_engine=no want_packages=no
case ${1:-} in
  --quick)    want_distros=no ;;
  --distros)  want_tests=no ;;
  --engine)   want_distros=no want_tests=no want_engine=yes ;;
  --packages) want_distros=no want_tests=no want_packages=yes ;;
  --all)      want_engine=yes want_packages=yes ;;
  "")         ;;
  *) echo "usage: $0 [--quick|--distros|--packages|--engine|--all]" >&2; exit 2 ;;
esac

failures=0
# Looked up once: both the distribution matrix and the package matrix need it,
# and two copies is two places to change when a third runtime appears.
runner=$(container_runner) || runner=""
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
  # scripts/ too, not just the application. The screenshot capture used to be
  # 175 lines held in a string constant inside another script, which no checker
  # could reach at all -- it is a real file now, and this is what makes that
  # worth something.
  if python3 -m compileall -q src tests scripts > /dev/null; then
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
  log=$(mktemp)
  # Kept and printed on failure. Discarding it turned "cmake could not find a
  # static libstdc++" into a bare red tick, and the whole point of building
  # here rather than trusting the published binary is to see why it broke.
  if ./scripts/build-engine.sh --output "$out" >"$log" 2>&1; then
    rm -f "$log"
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
    sed 's/^/    /' "$log"
    rm -f "$log"
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
  if [ -z "$runner" ]; then
    step "Distributions"
    echo "  (neither podman nor docker, skipped)"
  else
    # Built once, on the host, and the same way release.sh builds the published
    # asset -- so what gets installed below is the archive that ships. Doing it
    # inside each container instead would have tarred the working tree, which
    # carries untracked files and a gitignored 39 MB engine binary: three times
    # the work, on the one archive that cannot catch a file missing from the
    # release.
    #
    # HEAD, so this deliberately installs the committed tree -- uncommitted
    # edits are covered by the direct install.sh run below, which uses the
    # working copy. An edit that is only in the tree and never committed
    # failing here is the point, not a false alarm.
    tarball=$(mktemp -d)/nabria.tar.gz
    release_tarball HEAD > "$tarball"

    for entry in "${DISTROS[@]}"; do
      image=${entry%%|*}
      setup=${entry#*|}
      step "Distribution: $image"
      log=$(mktemp)
      if $runner run --rm -v "$project_dir":/src:ro,z -v "$tarball":/nabria.tar.gz:ro,z \
           "docker.io/library/$image" \
           bash -c "set -e
             $setup >/dev/null 2>&1
             cp -r /src /app && cd /app
             # The dependency check must pass with exactly the packages this
             # distribution's own hint names -- that is the thing being tested.
             ./scripts/install.sh --no-engine --no-model --no-service
             # Then the same install through the published path: the release
             # tarball, unpacked by bootstrap.sh. That is what someone running
             # the one-line command gets, and it is a different code path --
             # it strips a directory level and moves the tree into place before
             # running the installer from there. Mounted, not fetched: the
             # unpacking is under test here, not GitHub.
             NABRIA_ALLOW_ROOT=1 NABRIA_TARBALL=/nabria.tar.gz NABRIA_HOME=/opt/nabria \
               ./scripts/bootstrap.sh --no-engine --no-model --no-service
             test -x /opt/nabria/scripts/run.sh
             # Uninstall, and then what it must NOT have taken with it. Every
             # path in that list is a rm -rf on a directory inside someone's
             # home, so it is tested where a mistake costs a container and not
             # a 1.6 GB model and every transcript they have ever taken.
             mkdir -p \"\$HOME/.local/share/nabria/models\" \"\$HOME/.config/nabria\"
             touch \"\$HOME/.local/share/nabria/models/pretend.bin\"
             /opt/nabria/scripts/install.sh --uninstall
             test ! -e \"\$HOME/.local/bin/nabria\"
             test -f \"\$HOME/.local/share/nabria/models/pretend.bin\"
             test -f \"\$HOME/.config/nabria/config.json\"
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

# ------------------------------------------------------------------ packages

# The .rpm and .deb, installed by the distribution's own package manager on a
# machine that has never seen this project. That is the only way to find out
# whether the Requires: and Depends: lines name packages that exist -- the same
# reason the matrix above exists, applied to the dependency lists rather than to
# the installer's hints.
#
# Ubuntu is in here on purpose alongside Debian: it does not package
# gtk4-layer-shell at all, so it is the machine that proves the dependency is a
# Recommends. As a Depends the package would be uninstallable there, and the
# only place that shows is an apt run.
# Same two-field shape as DISTROS: the image, and the command that installs on
# it. Which package each takes is part of that command rather than a third
# field, because the pairing is fixed -- an rpm never goes near apt.
PACKAGE_TESTS=(
  "fedora:44|dnf install -y -q /pkg/nabria.rpm"
  "debian:trixie|apt-get update -qq && apt-get install -y -qq /pkg/nabria.deb"
  "ubuntu:24.04|apt-get update -qq && apt-get install -y -qq /pkg/nabria.deb"
)

if [ "$want_packages" = yes ]; then
  package_dir=$project_dir/dist
  # The names come from the file that decides them, so a renamed unit fails
  # this test instead of passing it against a stale literal.
  # shellcheck source=../packaging/layout.sh disable=SC1091
  . "$project_dir/packaging/layout.sh"
  if [ -z "$runner" ]; then
    step "Packages"; echo "  (neither podman nor docker, skipped)"
  elif [ ! -f "$package_dir/nabria.rpm" ] || [ ! -f "$package_dir/nabria.deb" ]; then
    # Skipped, not failed. `check.sh --all` on a machine that has not just run
    # package.sh is the normal case, and a red tick for it would train people
    # to ignore the colour.
    step "Packages"; echo "  (nothing in dist/ — run scripts/package.sh first, skipped)"
  else
    for entry in "${PACKAGE_TESTS[@]}"; do
      image=${entry%%|*}; installer=${entry#*|}
      step "Package on $image"
      log=$(mktemp)
      if $runner run --rm -v "$package_dir":/pkg:ro,z "docker.io/library/$image" \
           bash -c "set -e
             $installer
             # The unit name is what xdg-desktop-portal derives the app id
             # from, so a package that installs it under any other name gives
             # a daemon that runs and shortcuts that never fire.
             test -f /usr/lib/systemd/user/$UNIT_NAME
             test -L /usr/lib/systemd/user/nabria.service
             test -f /usr/share/applications/$APP_ID.desktop
             # run.sh sources this for layer_shell_library. Without it the
             # daemon does not start at all, by design -- so a package that
             # forgot to ship it is a package that cannot run.
             test -f /usr/lib/nabria/common.sh
             # The launcher has to find the code without PYTHONPATH being set
             # for it -- the packaged layout is not the checkout layout, and
             # this is the line that proves run.sh got that right.
             /usr/bin/nabria --nonsense 2>&1 | grep -q 'usage: python3 -m nabria'
             # The string table ships, and imports without GTK -- which is
             # what makes a control command a socket write rather than a
             # toolkit load. A package carrying the code but not the
             # translations would run, in English, with no error anywhere.
             PYTHONPATH=/usr/lib/nabria python3 -c 'from nabria import i18n; assert i18n.use(\"ar\") == \"ar\"; assert i18n.t(\"wizard.done\") != \"wizard.done\"'
             # And the bundled engine has to start, which is a question about
             # this distribution's libraries, not about the build.
             /usr/libexec/nabria/whisper-server --help >/dev/null
           " >"$log" 2>&1
      then
        pass "$image installed and runs"
      else
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
