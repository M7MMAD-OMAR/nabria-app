"""The models on offer, and how to fetch one.

Three, not thirty. Whisper ships a dozen sizes and half of them are
distinctions nobody dictating into a document can act on; the choice that
matters is how much machine you have. So the catalogue is small, the
recommendation is made from the hardware rather than left to the reader, and
the sizes are stated in the units people quit over -- gigabytes and seconds.

Checksums are the upstream Git-LFS oids, which are plain sha256 of the file
contents. Verified against locally downloaded copies of all three.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
CHUNK = 1 << 20


class Model(NamedTuple):
    key: str
    filename: str
    size: int
    sha256: str
    # An i18n key, not a sentence -- render it with `i18n.t(model.summary)`.
    # An English sentence here was a user-interface decision sitting in a data
    # catalogue without being labelled as one; this at least says so.
    summary: str
    needs_gpu: bool

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.filename}"

    @property
    def megabytes(self) -> int:
        return round(self.size / 1_000_000)


CATALOG: dict[str, Model] = {
    "base": Model(
        key="base",
        filename="ggml-base.bin",
        size=147_951_465,
        sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        summary="model.base.summary",
        needs_gpu=False,
    ),
    "small": Model(
        key="small",
        filename="ggml-small.bin",
        size=487_601_967,
        sha256="1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        summary="model.small.summary",
        needs_gpu=False,
    ),
    "large-v3-turbo": Model(
        key="large-v3-turbo",
        filename="ggml-large-v3-turbo.bin",
        size=1_624_555_275,
        sha256="1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
        summary="model.large-v3-turbo.summary",
        needs_gpu=True,
    ),
}

DEFAULT_WITH_GPU = "large-v3-turbo"
DEFAULT_WITHOUT_GPU = "base"


def recommended(has_discrete_gpu: bool) -> Model:
    """Chosen by hardware, because the wrong answer here is not a preference.

    Measured on 11 s of audio: large-v3-turbo takes 0.32 s on a discrete GPU
    and 21 s on a CPU -- half realtime, so a minute of dictation costs two
    minutes of waiting. `base` on the same CPU takes 0.9 s.
    """
    return CATALOG[DEFAULT_WITH_GPU if has_discrete_gpu else DEFAULT_WITHOUT_GPU]


def best_installed(model_dir: Path, has_gpu: bool | None = None) -> Path | None:
    """The best model actually on disk, or None.

    "Best" is not "biggest". large-v3-turbo is 21 s per 11 s of audio without a
    discrete GPU -- so on a machine that has both it and `base` installed,
    picking the larger one is picking the unusable one. The whole point of
    `recommended` is that this is a hardware question, not a preference.

    `has_gpu=None` means the caller could not afford to find out (asking costs
    a subprocess). Then the safe answer is the largest model that does not want
    a GPU, because being wrong that way costs some accuracy and being wrong the
    other way costs a tool that runs slower than speech.
    """
    present = {path.name: path for path in models_in(model_dir)}
    if not present:
        return None

    known = [model for model in CATALOG.values() if model.filename in present]
    if has_gpu is not None:
        preferred = recommended(has_gpu)
        if preferred.filename in present:
            return present[preferred.filename]
    else:
        known = [model for model in known if not model.needs_gpu] or known

    if known:
        return present[max(known, key=lambda model: model.size).filename]
    # Something dropped into the directory by hand; nothing to rank it by.
    return max(present.values(), key=lambda path: path.stat().st_size)


def models_in(model_dir: Path) -> list[Path]:
    """Finished `.bin` files in a directory. Mirrors `config.models`."""
    try:
        found = sorted(model_dir.glob("*.bin"))
    except OSError:
        return []
    return [
        path
        for path in found
        if not path.with_suffix(path.suffix + ".aria2").exists()
        and not path.with_suffix(path.suffix + ".part").exists()
    ]


def installed(model_dir: Path, model: Model) -> bool:
    path = model_dir / model.filename
    # Size alone, not the checksum: hashing 1.6 GB on every settings window
    # open is a visible pause, and a truncated download has the wrong size.
    return path.exists() and path.stat().st_size == model.size


def verify(path: Path, model: Model) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest() == model.sha256


class DownloadError(RuntimeError):
    pass


def download(
    model: Model,
    model_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Fetch a model, resuming a previous attempt if there is one.

    Resume matters more than it looks: these are hundreds of megabytes over
    connections that are often slow, and starting again from zero after a
    dropped link is where people give up. The partial file carries a `.part`
    suffix so `config.models()` will not offer it to the model picker while it
    is incomplete.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / model.filename
    partial = model_dir / f"{model.filename}.part"

    if target.exists() and target.stat().st_size == model.size:
        return target

    done = partial.stat().st_size if partial.exists() else 0
    if done >= model.size:
        # Already as long as the finished file, so there is nothing to ask for
        # and a Range request would be answered 416. Either it is the whole
        # model and the process died before the rename, or it is corrupt --
        # and a corrupt one has to go, or every future run repeats this and
        # the model can never be installed again.
        if done == model.size and verify(partial, model):
            partial.replace(target)
            return target
        partial.unlink()
        done = 0

    request = urllib.request.Request(model.url)
    if done:
        request.add_header("Range", f"bytes={done}-")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            # A server that ignores the Range header answers 200 and starts
            # from the beginning. Appending that to what we already have would
            # produce a file of the right length made of the wrong bytes --
            # which the checksum would catch, after the whole download.
            if done and response.status != 206:
                done = 0
                partial.unlink(missing_ok=True)
            mode = "ab" if done else "wb"
            with partial.open(mode) as sink:
                while True:
                    if should_cancel and should_cancel():
                        raise DownloadError("cancelled")
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    sink.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, model.size)
    except urllib.error.URLError as exc:
        raise DownloadError(f"could not reach {BASE_URL}: {exc.reason}") from exc
    except OSError as exc:
        raise DownloadError(str(exc)) from exc

    if partial.stat().st_size != model.size:
        raise DownloadError(
            f"expected {model.size} bytes, got {partial.stat().st_size}"
        )
    if not verify(partial, model):
        # Keeping it would mean the next run resumes from corrupt bytes and
        # fails the same way forever.
        partial.unlink(missing_ok=True)
        raise DownloadError("checksum mismatch; the download was corrupt")

    partial.replace(target)
    return target


def _cli() -> int:
    """`python3 -m nabria.models [key]` -- used by scripts/install.sh."""
    import sys

    from . import config, gpu

    if len(sys.argv) > 1 and sys.argv[1] == "--recommend":
        print(recommended(gpu.plan("auto").use_gpu).key)
        return 0

    key = sys.argv[1] if len(sys.argv) > 1 else recommended(gpu.plan("auto").use_gpu).key
    model = CATALOG.get(key)
    if model is None:
        print(f"unknown model {key!r}; choose from {', '.join(CATALOG)}", file=sys.stderr)
        return 2

    if installed(config.MODEL_DIR, model):
        print(f"{model.filename} already installed")
        return 0

    width = 40

    def progress(done: int, total: int) -> None:
        filled = int(width * done / max(total, 1))
        print(
            f"\r  [{'#' * filled}{'.' * (width - filled)}] "
            f"{done // 1_000_000}/{total // 1_000_000} MB",
            end="", flush=True,
        )

    print(f"downloading {model.filename} ({model.megabytes} MB)")
    try:
        path = download(model, config.MODEL_DIR, progress)
    except DownloadError as exc:
        print(f"\nfailed: {exc}", file=sys.stderr)
        return 1
    print(f"\n  verified -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
