#!/usr/bin/env python3
"""Install the optional, pinned Linux streaming runtime without changing Whisper."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nabria import config, models  # noqa: E402
from nabria.nemotron import binary_path  # noqa: E402

ARCHIVE_URL = (
    "https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.3.0/"
    "nemo-speech-0.3.0-linux-x86_64-vulkan.tar.gz"
)
ARCHIVE_SHA256 = "2a8b2ca4610b6ea5d659ba10794526f6e5a77fb1623d52c35272a9fd1f74abac"
ARCHIVE_ROOT = "nemo-speech-0.3.0-linux-x86_64-vulkan"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use a previously downloaded release archive")
    parser.add_argument("--model-file", type=Path, help="Use previously downloaded GGUF weights")
    parser.add_argument("--activate", action="store_true", help="Select the model in config; restart separately when idle")
    args = parser.parse_args()
    if not hasattr(tarfile, "data_filter"):
        parser.error("use a maintained Python with tarfile.data_filter (Python 3.12+ or a security-patched 3.10/3.11)")
    if sys.platform != "linux" or __import__("platform").machine() not in {"x86_64", "amd64"}:
        parser.error("this pinned runtime is for Linux x86_64")
    if importlib.util.find_spec("websockets") is None:
        parser.error("install websockets 15 or newer for live preview first")
    from importlib.metadata import version
    if int(version("websockets").split(".")[0]) < 15:
        parser.error("live preview requires websockets 15 or newer")

    config.LIBEXEC_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nemo-install-", dir=config.LIBEXEC_DIR) as temporary:
        staging = Path(temporary)
        archive = args.archive or staging / "runtime.tar.gz"
        if args.archive is None:
            with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("wb") as sink:
                shutil.copyfileobj(response, sink)
        checksum = hashlib.sha256()
        with archive.open("rb") as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                checksum.update(block)
        digest = checksum.hexdigest()
        if digest != ARCHIVE_SHA256:
            parser.error("runtime archive checksum mismatch")
        with tarfile.open(archive) as package:
            package.extractall(staging, filter="data")
        ready = staging / ARCHIVE_ROOT
        if not (ready / "bin" / "nemo-speech").is_file():
            parser.error("runtime archive is incomplete")
        destination = binary_path().parent.parent
        previous = destination.with_name(f".nemo-previous-{uuid.uuid4().hex}")
        if destination.exists():
            destination.rename(previous)
        try:
            ready.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
        if previous.exists():
            shutil.rmtree(previous)

    target = config.MODEL_DIR / models.NEMOTRON.filename
    if args.model_file is not None:
        if args.model_file.stat().st_size != models.NEMOTRON.size or not models.verify(args.model_file, models.NEMOTRON):
            parser.error("model checksum or size mismatch")
        config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if args.model_file.resolve() != target.resolve():
            partial = target.with_suffix(target.suffix + ".part")
            shutil.copyfile(args.model_file, partial)
            partial.replace(target)
    else:
        target = models.download(models.NEMOTRON, config.MODEL_DIR)
    if args.activate:
        settings = config.load()
        if config.CONFIG_PATH.exists():
            backup = config.CONFIG_PATH.with_name(f"config.before-streaming-{uuid.uuid4().hex}.json")
            shutil.copyfile(config.CONFIG_PATH, backup)
            print(f"Previous configuration: {backup}")
        settings["model"] = str(target)
        settings["nemo_binary"] = str(binary_path())
        config.save(settings)
    print(f"Streaming runtime: {binary_path()}")
    print(f"Streaming model: {target}")
    if args.activate:
        print("Restart Nabria when idle to apply the selected model.")


if __name__ == "__main__":
    main()
