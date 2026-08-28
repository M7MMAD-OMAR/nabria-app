#!/bin/bash
# Installs the launchers, the systemd unit, and this tool's own copy of the
# transcription engine.
#
# The engine is copied out of OpenWhispr's directories on purpose: sharing them
# would mean uninstalling OpenWhispr silently breaks dictation. The model is
# hard-linked when both paths are on one filesystem, so the 1.6 GB is not
# duplicated -- and the link keeps the data alive even if the cache is cleared.
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

SERVER_SOURCE=${SERVER_SOURCE:-$HOME/.config/open-whispr/bin/whisper-server-linux-x64-vulkan}
MODEL_SOURCE=${MODEL_SOURCE:-$HOME/.cache/openwhispr/whisper-models/ggml-large-v3-turbo.bin}

libexec_dir=$HOME/.local/libexec/nabria
model_dir=$HOME/.local/share/nabria/models
bin_dir=$HOME/.local/bin
unit_dir=$HOME/.config/systemd/user

mkdir -p "$libexec_dir" "$model_dir" "$bin_dir" "$unit_dir"

install_engine() {
  local source=$1 target=$2 label=$3
  if [ -e "$target" ]; then
    echo "$label: already installed"
    return
  fi
  if [ ! -e "$source" ]; then
    echo "$label: NOT FOUND at $source" >&2
    echo "  set ${4} to point at it, then re-run" >&2
    return 1
  fi
  if ln "$source" "$target" 2>/dev/null; then
    echo "$label: hard-linked from $source"
  else
    cp -- "$source" "$target"
    echo "$label: copied from $source"
  fi
}

install_engine "$SERVER_SOURCE" "$libexec_dir/whisper-server" "whisper-server" SERVER_SOURCE
chmod +x "$libexec_dir/whisper-server"
install_engine "$MODEL_SOURCE" "$model_dir/$(basename "$MODEL_SOURCE")" "model" MODEL_SOURCE

cat > "$bin_dir/nabria" <<LAUNCHER
#!/bin/sh
exec '$project_dir'/scripts/run.sh "\$@"
LAUNCHER
chmod +x "$bin_dir/nabria"

ln -sf "$bin_dir/nabria" "$bin_dir/nabria-toggle"

sed "s#%h#$HOME#g" "$project_dir/systemd/nabria.service" > "$unit_dir/nabria.service"
systemctl --user daemon-reload

PROJECT_FEDORA="$project_dir/src" python3 - <<'PY'
import sys
sys.path.insert(0, __import__("os").environ["PROJECT_FEDORA"])
from nabria import config
print("config:", config.write_default_config())
PY

echo
echo "installed. next:"
echo "  systemctl --user enable --now nabria"
echo "  nabria status"
