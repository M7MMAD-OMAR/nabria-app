#!/bin/bash
# Installs Nabria: dependency check, transcription engine, model, autostart.
#
# Safe to re-run. Anything already in place is left alone, so this doubles as
# the repair path when one piece is missing.
#
#   scripts/install.sh [--model KEY] [--no-model] [--no-engine] [--no-service]
set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"

libexec_dir=$HOME/.local/libexec/nabria
bin_dir=$HOME/.local/bin
unit_dir=$HOME/.config/systemd/user

model_key=""
do_model=yes
do_engine=yes
do_service=yes

while [ $# -gt 0 ]; do
  case $1 in
    --model) model_key=$2; shift 2 ;;
    --no-model) do_model=no; shift ;;
    --no-engine) do_engine=no; shift ;;
    --no-service) do_service=no; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# Package names differ per distro and guessing wrong sends people down a
# dead end, so the hint is chosen from what is actually installed here.
if   command -v dnf     >/dev/null 2>&1; then family=fedora
elif command -v apt-get >/dev/null 2>&1; then family=debian
elif command -v pacman  >/dev/null 2>&1; then family=arch
else family=unknown
fi

hint() {
  case $family in
    fedora) echo "sudo dnf install $1" ;;
    debian) echo "sudo apt install $2" ;;
    arch)   echo "sudo pacman -S $3" ;;
    *)      echo "install: $1" ;;
  esac
}

# ---------------------------------------------------------------- requirements

say "Checking what is here"
fatal=0

if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  ok "python $(python3 -c 'import platform;print(platform.python_version())')"
else
  bad "python 3.10 or newer is required"; fatal=1
fi

if python3 -c 'import gi; gi.require_version("Gtk","4.0"); from gi.repository import Gtk' 2>/dev/null; then
  ok "GTK 4 with Python bindings"
else
  bad "GTK 4 Python bindings are missing"
  echo "      $(hint 'python3-gobject gtk4' 'python3-gi gir1.2-gtk-4.0' 'python-gobject gtk4')"
  fatal=1
fi

# The indicator is a layer-shell surface. Without this it silently becomes an
# ordinary window that fullscreen apps cover -- which is the entire reason this
# tool exists rather than the one it replaced.
# Each candidate tested on its own: `ls a b c` fails when *any* argument is
# missing, so one absent path reported the library as absent even where it was
# installed.
layer_shell=""
for candidate in /usr/lib64/libgtk4-layer-shell.so.0 \
                 /usr/lib/libgtk4-layer-shell.so.0 \
                 /usr/lib/*/libgtk4-layer-shell.so.0; do
  [ -e "$candidate" ] && { layer_shell=$candidate; break; }
done
# The library alone is not enough -- PyGObject needs the typelib, which Debian
# ships in a *separate* package. Installing only the library there gets you a
# working install with no indicator and nothing saying why, so both are checked
# and both are named.
if python3 -c 'import gi; gi.require_version("Gtk4LayerShell","1.0")' 2>/dev/null; then
  ok "gtk4-layer-shell"
elif [ -n "$layer_shell" ]; then
  warn "gtk4-layer-shell is installed but its typelib is not"
  echo "      $(hint gtk4-layer-shell gir1.2-gtk4layershell-1.0 gtk4-layer-shell)"
else
  warn "gtk4-layer-shell not found — the indicator will fall back to a plain window"
  echo "      $(hint gtk4-layer-shell 'libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0' gtk4-layer-shell)"
  if [ "$family" = debian ] && ! apt-cache show libgtk4-layer-shell0 >/dev/null 2>&1; then
    # True on Ubuntu 24.04, which packages only the GTK3 version. Saying so
    # beats letting someone hunt for a package that is not there.
    echo "      (not packaged on this release — the indicator will be a plain window)"
  fi
fi

if command -v pw-record >/dev/null 2>&1; then
  ok "PipeWire (pw-record)"
else
  bad "pw-record is missing — there is no way to record without it"
  echo "      $(hint pipewire-utils pipewire-bin pipewire-audio)"
  fatal=1
fi

if command -v wl-copy >/dev/null 2>&1; then
  ok "wl-clipboard"
else
  bad "wl-copy is missing — pasting the transcript needs it"
  echo "      $(hint wl-clipboard wl-clipboard wl-clipboard)"
  fatal=1
fi

if command -v wtype >/dev/null 2>&1 || command -v ydotool >/dev/null 2>&1; then
  ok "a way to send keystrokes ($(command -v wtype >/dev/null 2>&1 && echo wtype || echo ydotool))"
else
  bad "neither wtype nor ydotool — nothing can send the paste keystroke"
  echo "      $(hint wtype wtype wtype)"
  fatal=1
fi

[ "$fatal" -eq 0 ] || { echo; echo "Install the missing pieces above and run this again." >&2; exit 1; }

# ---------------------------------------------------------------------- engine

if [ "$do_engine" = yes ]; then
  if [ -x "$libexec_dir/whisper-server" ]; then
    say "Engine"; ok "already installed at $libexec_dir/whisper-server"
  else
    say "Building the transcription engine"
    echo "  whisper.cpp from source. This takes a few minutes, once."
    "$project_dir/scripts/build-engine.sh" --output "$libexec_dir/whisper-server"
  fi
fi

# ----------------------------------------------------------------------- model

if [ "$do_model" = yes ]; then
  say "Model"
  if [ -z "$model_key" ]; then
    model_key=$(python3 -m nabria.models --recommend)
    echo "  Chosen for this machine: $model_key"
  fi
  python3 -m nabria.models "$model_key"
fi

# -------------------------------------------------------------------- launcher

say "Command and autostart"
mkdir -p "$libexec_dir" "$bin_dir" "$unit_dir"

cat > "$bin_dir/nabria" <<LAUNCHER
#!/bin/sh
exec '$project_dir'/scripts/run.sh "\$@"
LAUNCHER
chmod +x "$bin_dir/nabria"
ok "nabria -> $bin_dir/nabria"

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) warn "$bin_dir is not on your PATH; add it to your shell profile" ;;
esac

if [ "$do_service" = yes ]; then
  sed "s#@RUN_SH@#$project_dir/scripts/run.sh#g" \
    "$project_dir/systemd/nabria.service" > "$unit_dir/nabria.service"
  systemctl --user daemon-reload
  ok "systemd unit written"
fi

python3 -c 'from nabria import config; print("  config:", config.write_default_config())'

# A desktop entry, so Nabria appears in application menus and so compositors
# can tie its window to a name and an icon. Without one it is a command that
# exists only if you already know it exists.
applications_dir=$HOME/.local/share/applications
mkdir -p "$applications_dir"
sed "s#^Exec=nabria#Exec=$bin_dir/nabria#" \
  "$project_dir/share/com.sbarah.Nabria.desktop" \
  > "$applications_dir/com.sbarah.Nabria.desktop"
command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database "$applications_dir" 2>/dev/null || true
ok "desktop entry installed"

# -------------------------------------------------------------------- shortcut

say "One thing left: the shortcut"
cat <<'EOS'
  Wayland has no cross-desktop way for an application to claim a global
  hotkey, so this part is yours. Bind these to whatever you like:

    nabria toggle          start and stop dictating
    nabria cancel          throw the current take away
    nabria settings        model, microphone, history
EOS

# Detected and phrased by the same module the setup wizard uses, so the two
# can never drift into telling people different things.
python3 -c 'from nabria import shortcut; print("\n".join("  " + line for line in shortcut.instructions()))'

say "Then"
echo "  systemctl --user enable --now nabria"
echo "  nabria status"
