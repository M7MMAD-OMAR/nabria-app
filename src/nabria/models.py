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
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
CHUNK = 1 << 20

# The first four bytes of every ggml file. whisper.cpp writes GGML_FILE_MAGIC
# (0x67676d6c) as a host-endian uint32, so on a little-endian machine it lands
# in this order; checked against all three catalogue models.
#
# Four bytes being the whole test is the point. It is what lets a scan of
# several directories stay instant while still refusing a `.bin` that is a
# firmware blob, a disk image, or another project's weights -- and a wrong
# answer there would be reported by whisper-server failing to start, which is
# the least legible error this program can produce.
GGML_MAGIC = b"lmgg"


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

    The largest model measured slower than speech on a CPU -- a minute of
    dictation costing more than a minute of waiting -- and comfortably faster
    than speech on a discrete GPU. The smallest was comfortable on both. No
    figures: they were one machine's, and how fast a model runs is a property
    of somebody's hardware rather than of this software.
    """
    return CATALOG[DEFAULT_WITH_GPU if has_discrete_gpu else DEFAULT_WITHOUT_GPU]


def best_installed(model_dir: Path, has_gpu: bool | None = None) -> Path | None:
    """The best model actually on disk, or None.

    "Best" is not "biggest". The largest model runs slower than speech without
    a discrete GPU, so on a machine that has both it and the smallest one
    installed, picking the larger is picking the unusable one. The whole point
    of `recommended` is that this is a hardware question, not a preference.

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
        # `exists()` follows the link, and that is the reason it is here:
        # `adopt` can leave a symbolic link to a model somewhere else, and
        # emptying that somewhere else leaves a name that still globs but
        # cannot be opened. Offering it would hand the engine a dead path,
        # which fails as "whisper-server did not start" rather than as the
        # missing model it actually is.
        if path.exists()
        and not path.with_suffix(path.suffix + ".aria2").exists()
        and not path.with_suffix(path.suffix + ".part").exists()
    ]


def installed(model_dir: Path, model: Model) -> bool:
    path = model_dir / model.filename
    # Size alone, not the checksum: hashing 1.6 GB on every settings window
    # open is a visible pause, and a truncated download has the wrong size.
    return path.exists() and path.stat().st_size == model.size


def verify(
    path: Path, model: Model, on_progress: Callable[[int, int], None] | None = None
) -> bool:
    digest = hashlib.sha256()
    done = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, model.size)
    return digest.hexdigest() == model.sha256


class DownloadError(RuntimeError):
    pass


# -- models that are already on the machine ---------------------------------
#
# The largest model is 1.6 GB. Someone who has already fetched one -- for a
# different program, from a checkout, or by hand into their downloads folder --
# should not spend that a second time on the connection and a third time on the
# disk. So the wizard looks before it offers to download, and a found model is
# linked into place rather than copied.


class Found(NamedTuple):
    """A whisper model sitting somewhere else on this machine."""

    path: Path
    # The catalogue entry, when the size identifies one. `None` for anything
    # else that is a real ggml file -- a size or a quantisation this program
    # does not offer. Those are usable and are offered, but there is no
    # published checksum to hold them to, and the interface has to say so.
    model: Model | None

    @property
    def name(self) -> str:
        if self.model is not None:
            return self.model.key
        return self.path.stem.removeprefix("ggml-")


_BY_SIZE = {model.size: model for model in CATALOG.values()}


def search_roots() -> list[tuple[Path, str]]:
    """Where a whisper model already on this machine tends to sit.

    Only locations belonging to the *format*: whisper.cpp's own conventional
    directories, the cache the models are published into, and the folder a
    browser saves to. Directories belonging to particular programs are
    deliberately not listed -- the list would go stale, and it would put other
    people's product names in this file and in anything describing the feature.

    Nothing here walks `$HOME`. A recursive scan of a home directory to fill in
    one page of a setup wizard is not a trade this program makes: it reads
    every name a person has, and on a large disk it does not finish.
    """
    home = Path.home()
    cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    return [
        (home / "whisper.cpp/models", "*.bin"),
        (home / "src/whisper.cpp/models", "*.bin"),
        (cache / "whisper.cpp", "*.bin"),
        (data / "whisper.cpp/models", "*.bin"),
        (Path("/usr/share/whisper.cpp/models"), "*.bin"),
        # The published cache keeps the *named* file under `snapshots/` as a
        # symlink into `blobs/`, whose own names are content hashes with no
        # extension -- so the named copy is the only one worth globbing for.
        # The pattern is shaped rather than `**`: a cache holding hundreds of
        # unrelated repositories would otherwise be walked in full, on a page
        # that has to appear the moment it is asked for.
        (cache / "huggingface/hub", "models--*[wW]hisper*/snapshots/*/*.bin"),
        (home / "Downloads", "*.bin"),
    ]


def looks_like_a_model(path: Path) -> bool:
    """Whether this is a ggml file at all. Four bytes, so it is free."""
    try:
        with path.open("rb") as source:
            return source.read(4) == GGML_MAGIC
    except OSError:
        return False


def identify(path: Path) -> Model | None:
    """Which catalogue model a file is, by size.

    Size and not name: a copy renamed on its way into somebody's downloads
    folder is still the same 1,624,555,275 bytes, and the three sizes are
    hundreds of megabytes apart, so there is nothing for a coincidence to land
    on. The checksum is the real answer, and `adopt` asks for it once, when a
    file is actually being taken -- not here, where it would mean hashing every
    candidate to draw a page.
    """
    try:
        return _BY_SIZE.get(path.stat().st_size)
    except OSError:
        return None


def search(
    roots: list[tuple[Path, str]] | None = None, model_dir: Path | None = None
) -> list[Found]:
    """Every whisper model already on this machine, outside our own directory.

    `roots` is injectable so tests can point it somewhere temporary; left alone
    it is `search_roots()`.
    """
    # Identity is the inode, not the path. A model this program adopted is
    # reachable under both its original name and ours, and the published cache
    # publishes one blob under several snapshot names -- all of which are one
    # model, and would otherwise be offered two and three times over.
    ours: set[tuple[int, int]] = set()
    have: set[str] = set()
    if model_dir is not None:
        for path in models_in(model_dir):
            try:
                info = path.stat()
            except OSError:
                continue
            ours.add((info.st_dev, info.st_ino))
        have = {
            model.filename for model in CATALOG.values() if installed(model_dir, model)
        }

    seen: set[tuple[int, int]] = set(ours)
    found: list[Found] = []
    for directory, pattern in search_roots() if roots is None else roots:
        try:
            candidates = sorted(directory.glob(pattern))
        except OSError:
            continue
        for path in candidates:
            try:
                info = path.stat()  # follows the link, so a symlink is its target
            except OSError:
                continue  # a dangling link, or one we may not read
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            entry = _BY_SIZE.get(info.st_size)
            # A separate copy of a model we already hold. Same bytes, different
            # inode, and adopting it would change nothing.
            if entry is not None and entry.filename in have:
                continue
            if looks_like_a_model(path):
                found.append(Found(path, entry))
    return found


def adopt(
    source: Path,
    model_dir: Path,
    model: Model | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Take a model that is already on this machine, without a second copy.

    A link, never a copy. These files run to 1.6 GB and the whole reason for
    pointing at one that is already here is to not spend that twice.

    A hard link is tried first because it is the one that survives: the
    original can be deleted, the downloads folder emptied, and the model is
    still here and still a single copy of the bytes. It fails across
    filesystems, and on a root-owned file under a kernel with
    `fs.protected_hardlinks` -- Fedora's default -- so a symbolic link is the
    fallback, at the cost that removing the original removes the model.
    `models_in` drops a link whose target has gone rather than offering the
    engine a path it cannot open.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    # The named file in a published cache is itself a symlink into a blob
    # store. Linking to the link would leave the model one cache eviction away
    # from being a dead path, whichever kind of link we then made.
    source = source.resolve()
    target = model_dir / (model.filename if model is not None else source.name)
    if target.exists():
        return target

    if not looks_like_a_model(source):
        raise DownloadError("not a whisper model file")
    # Recognised files are held to exactly the standard a downloaded one is.
    # An unrecognised one cannot be -- there is no published copy to compare it
    # against -- and the interface says so rather than implying a check.
    if model is not None and not verify(source, model, on_progress):
        raise DownloadError("checksum mismatch; this is not the model it looks like")

    try:
        os.link(source, target)
    except OSError:
        try:
            target.symlink_to(source)
        except OSError as exc:
            raise DownloadError(str(exc)) from exc
    return target


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
