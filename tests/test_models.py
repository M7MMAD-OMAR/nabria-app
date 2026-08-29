"""The model catalogue and its downloader.

The download is served from a local HTTP server rather than mocked, because
the failures worth catching are protocol-level: a resume that appends to the
wrong offset, a server that ignores Range, a truncated file that passes for
finished.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from nabria import models


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """A file server that honours Range, because the real one does.

    Written out rather than using SimpleHTTPRequestHandler, which ignores
    Range and answers 200 with the whole file. Against that server the resume
    test passes without ever resuming -- it silently re-downloads and gets the
    right answer, which is exactly the kind of test that reports success while
    checking nothing.
    """

    root: Path
    serve_ranges = True

    def log_message(self, *args):  # noqa: A003 - quiet
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        path = self.root / self.path.lstrip("/")
        if not path.is_file():
            self.send_error(404)
            return
        payload = path.read_bytes()
        start = 0
        requested = self.headers.get("Range")
        if requested and self.serve_ranges and requested.startswith("bytes="):
            start = int(requested.removeprefix("bytes=").split("-")[0])
            if start >= len(payload):
                self.send_error(416)
                return

        body = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header(
                "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            )
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def served(tmp_path):
    root = tmp_path / "www"
    root.mkdir()

    handler = type("Rooted", (RangeHandler,), {"root": root})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.handle_error = lambda *args: None  # a cancelled download closes early
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{server.server_port}", handler
    server.shutdown()


def make_model(root: Path, payload: bytes, name: str = "ggml-test.bin") -> models.Model:
    (root / name).write_bytes(payload)
    return models.Model(
        key="test", filename=name, size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        summary="", needs_gpu=False,
    )


@pytest.fixture
def at_served(served, monkeypatch):
    root, base, _handler = served
    monkeypatch.setattr(models, "BASE_URL", base)
    return root


@pytest.fixture
def no_range(served):
    """Make the server ignore Range, the way some CDNs and proxies do."""
    _root, _base, handler = served
    handler.serve_ranges = False
    return handler


def test_catalogue_is_self_consistent():
    for key, model in models.CATALOG.items():
        assert model.key == key
        assert len(model.sha256) == 64
        assert model.size > 0
        assert model.filename.endswith(".bin")


def test_recommendation_follows_the_hardware():
    # large-v3-turbo is 21s per 11s of audio on a CPU. Recommending it to
    # someone without a discrete GPU is recommending a tool that does not work.
    assert models.recommended(True).key == "large-v3-turbo"
    assert models.recommended(False).needs_gpu is False


def test_download_and_verify(at_served, tmp_path):
    model = make_model(at_served, b"x" * 5000)
    path = models.download(model, tmp_path / "models")
    assert path.read_bytes() == b"x" * 5000
    assert not list((tmp_path / "models").glob("*.part"))


def test_progress_is_reported(at_served, tmp_path):
    model = make_model(at_served, b"y" * (3 << 20))
    seen: list[tuple[int, int]] = []
    models.download(model, tmp_path / "models", on_progress=lambda d, t: seen.append((d, t)))
    assert seen and seen[-1][0] == model.size
    assert all(total == model.size for _, total in seen)


def test_an_existing_complete_file_is_not_downloaded_again(at_served, tmp_path):
    payload = b"z" * 4000
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / model.filename).write_bytes(payload)
    (at_served / model.filename).unlink()  # nothing to download from

    assert models.download(model, target).read_bytes() == payload


def test_a_partial_download_resumes(at_served, tmp_path):
    payload = bytes(range(256)) * 400
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / f"{model.filename}.part").write_bytes(payload[:5000])

    transferred: list[int] = []
    path = models.download(
        model, target, on_progress=lambda done, total: transferred.append(done)
    )
    assert path.read_bytes() == payload
    # Progress starts from where the partial left off, which is the proof that
    # it resumed rather than quietly starting again and landing on the same
    # bytes -- the two are indistinguishable from the finished file alone.
    assert transferred[0] > 5000
    assert transferred[-1] == model.size


def test_a_server_that_ignores_range_still_produces_the_right_file(
    at_served, no_range, tmp_path
):
    # Answering 200 to a Range request means the body starts at zero. Appending
    # that to what we already have would give a file of the correct length made
    # of the wrong bytes, so the partial has to be thrown away first.
    payload = bytes(range(256)) * 400
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / f"{model.filename}.part").write_bytes(payload[:5000])

    assert models.download(model, target).read_bytes() == payload


def test_a_full_length_corrupt_partial_is_discarded_not_resumed_forever(at_served, tmp_path):
    # The nastiest case: the partial is already as long as the finished model,
    # so there is nothing left to request. Asking anyway gets a 416, which
    # reads as a network error and -- before this was fixed -- left the bad
    # file in place, so every later attempt failed the same way and the model
    # could never be installed again.
    payload = b"a" * 8000
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / f"{model.filename}.part").write_bytes(b"b" * 8000)

    assert models.download(model, target).read_bytes() == payload
    assert not (target / f"{model.filename}.part").exists()


def test_a_complete_partial_is_promoted_rather_than_refetched(at_served, tmp_path):
    # Killed between the last byte and the rename. The bytes are all there and
    # correct; re-downloading hundreds of megabytes to learn that would be a
    # poor way to find out.
    payload = b"a" * 8000
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / f"{model.filename}.part").write_bytes(payload)
    (at_served / model.filename).unlink()  # nothing to download from

    assert models.download(model, target).read_bytes() == payload


def test_a_short_corrupt_partial_is_caught_by_the_checksum(at_served, tmp_path):
    payload = b"a" * 8000
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    # Wrong bytes, but short enough that a resume looks reasonable. Only the
    # checksum at the end can tell.
    (target / f"{model.filename}.part").write_bytes(b"b" * 3000)

    with pytest.raises(models.DownloadError, match="checksum"):
        models.download(model, target)
    assert not (target / f"{model.filename}.part").exists()


def test_an_oversized_partial_is_thrown_away(at_served, tmp_path):
    payload = b"c" * 3000
    model = make_model(at_served, payload)
    target = tmp_path / "models"
    target.mkdir()
    (target / f"{model.filename}.part").write_bytes(b"d" * 9000)

    assert models.download(model, target).read_bytes() == payload


def test_the_partial_is_never_offered_to_the_model_picker(at_served, tmp_path, fresh_config):
    # A half-downloaded model in the picker loads a truncated file and fails
    # with something that reads like a broken engine.
    fresh_config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (fresh_config.MODEL_DIR / "ggml-half.bin").write_bytes(b"x")
    (fresh_config.MODEL_DIR / "ggml-half.bin.part").write_bytes(b"x")
    assert fresh_config.models() == []


def test_a_missing_file_reports_plainly(at_served, tmp_path):
    model = models.Model("gone", "ggml-absent.bin", 10, "0" * 64, "", False)
    with pytest.raises(models.DownloadError):
        models.download(model, tmp_path / "models")


def test_cancellation_stops_the_download(at_served, tmp_path):
    model = make_model(at_served, b"e" * (8 << 20))
    with pytest.raises(models.DownloadError, match="cancelled"):
        models.download(model, tmp_path / "models", should_cancel=lambda: True)


def test_installed_notices_a_truncated_file(tmp_path):
    model = models.CATALOG["base"]
    tmp_path.joinpath(model.filename).write_bytes(b"not the whole thing")
    assert models.installed(tmp_path, model) is False


def test_verify_rejects_the_wrong_contents(tmp_path):
    model = make_model(tmp_path, b"the real thing")
    tmp_path.joinpath(model.filename).write_bytes(b"something else")
    assert models.verify(tmp_path / model.filename, model) is False


def test_the_readme_states_the_catalogue_s_real_sizes():
    """The README's size table is the catalogue restated in another unit.

    Restated facts drift -- a model list in two places had both copies stale
    before, which is why nothing in this project is written down twice without
    something binding the copies. This is the binding: the README quotes MiB
    and GiB because those are the units upstream publishes, the catalogue
    holds exact bytes, and only one of the two gets updated when a model is
    replaced.

    Sizes are publishable and timings are not, which is the other half of why
    this table exists at all: how fast a model runs belongs to the reader's
    hardware, so the README states what each one *needs* instead.
    """
    import re
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    rows = dict(re.findall(r"\| `([\w.-]+)` \| ([\d.]+ [MG]iB) \|", readme))
    assert set(rows) == set(models.CATALOG), "the README table and the catalogue disagree"

    for key, stated in rows.items():
        amount, unit = stated.split()
        scale = 1024 ** 2 if unit == "MiB" else 1024 ** 3
        actual = models.CATALOG[key].size / scale
        # Half of the last digit the README chose to print, so a row written
        # to one decimal is held to one decimal and a whole number to a whole
        # number. A fixed tolerance would either wave through a wrong figure
        # or fail an honestly rounded one.
        _, _, decimals = amount.partition(".")
        assert abs(actual - float(amount)) <= 0.5 * 10 ** -len(decimals), (
            f"README says {key} is {stated}, catalogue says {actual:.2f} {unit}"
        )


# -- models that are already on the machine ---------------------------------
#
# The wizard offers to link one of these rather than spending a second
# download and a second copy of the disk on bytes that are already here. What
# these check is that it finds them, refuses what only looks like one, and
# never quietly accepts a file that fails the published checksum.


def a_model_file(path: Path, size: int = 4) -> Path:
    """A file that begins the way every ggml file does, of an exact size.

    Sparse: the catalogue is identified by size, so these tests need files of
    148 MB and 488 MB, and writing those for real filled /tmp and failed the
    suite with a quota error that looked nothing like a test failure. The hole
    reads back as the zeroes the checksum tests already expect.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as sink:
        sink.write(models.GGML_MAGIC)
        if size > len(models.GGML_MAGIC):
            sink.truncate(size)
    return path


def test_the_magic_is_what_the_catalogue_models_actually_start_with():
    """Not taken on trust.

    whisper.cpp writes GGML_FILE_MAGIC as a host-endian integer, so the byte
    order in the file is the machine's, not the constant's. Building the same
    four bytes the same way here is what would catch a big-endian build -- and
    the constant being wrong would mean the search silently finds nothing at
    all, which reads exactly like "there is nothing on this machine".
    """
    assert models.GGML_MAGIC == (0x67676D6C).to_bytes(4, "little")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(models.CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def test_a_bin_file_that_is_not_a_model_is_refused(tmp_path):
    # The case this exists for: a firmware blob, a disk image, another
    # project's weights. Accepting one gets reported as "whisper-server did
    # not start", which says nothing about the actual mistake.
    (tmp_path / "firmware.bin").write_bytes(b"\x7fELF" + b"\0" * 64)
    assert models.looks_like_a_model(tmp_path / "firmware.bin") is False


def test_a_model_is_identified_by_size_not_by_name(tmp_path):
    base = models.CATALOG["base"]
    renamed = a_model_file(tmp_path / "speech.bin", base.size)
    assert models.identify(renamed) is base


def test_search_finds_a_model_and_names_it_from_the_catalogue(tmp_path, models_dir):
    base = models.CATALOG["base"]
    a_model_file(tmp_path / "elsewhere/ggml-base.bin", base.size)

    found = models.search([(tmp_path / "elsewhere", "*.bin")], models_dir)
    assert [(entry.name, entry.model) for entry in found] == [("base", base)]


def test_search_offers_an_unknown_model_without_pretending_to_know_it(
    tmp_path, models_dir
):
    # A size or a quantisation this program does not publish. Usable, but there
    # is no checksum to hold it to, and `model=None` is how the page knows to
    # say so.
    a_model_file(tmp_path / "ggml-medium.bin", 1004)
    found = models.search([(tmp_path, "*.bin")], models_dir)
    assert len(found) == 1
    assert found[0].model is None
    assert found[0].name == "medium"


def test_one_file_reached_by_two_names_is_one_model(tmp_path, models_dir):
    """The published cache links one blob into several snapshot directories."""
    real = a_model_file(tmp_path / "blobs/abc123", 104)
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots/ggml-base.bin").symlink_to(real)

    found = models.search(
        [(tmp_path / "snapshots", "*.bin"), (tmp_path / "blobs", "*")], models_dir
    )
    assert len(found) == 1


def test_a_model_already_adopted_is_not_offered_again(tmp_path, models_dir):
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    models.adopt(source, models_dir)
    assert models.search([(tmp_path, "*.bin")], models_dir) == []


def test_a_model_already_downloaded_is_not_offered_again(tmp_path, models_dir):
    # A separate copy this time -- different inode, same bytes. Adopting it
    # would change nothing, so it does not appear.
    base = models.CATALOG["base"]
    a_model_file(models_dir / base.filename, base.size)
    a_model_file(tmp_path / "ggml-base.bin", base.size)
    assert models.search([(tmp_path, "*.bin")], models_dir) == []


def test_a_missing_search_directory_is_not_an_error(models_dir, tmp_path):
    # Most of the real list does not exist on most machines.
    assert models.search([(tmp_path / "nowhere", "*.bin")], models_dir) == []


def test_adopt_links_rather_than_copying(tmp_path, models_dir):
    """The point of the whole feature: 1.6 GB is not spent twice.

    Checked through the link count rather than through free space, which is
    the only measurement that is the same on every filesystem.
    """
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    target = models.adopt(source, models_dir)
    assert target.stat().st_nlink == 2
    assert target.read_bytes() == source.read_bytes()


def test_an_adopted_model_survives_the_original_being_deleted(tmp_path, models_dir):
    # Which is why a hard link is tried first. Somebody who empties their
    # downloads folder a week later has not uninstalled their model.
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    target = models.adopt(source, models_dir)
    source.unlink()
    assert target.exists()
    assert models.models_in(models_dir) == [target]


def test_adopt_falls_back_to_a_symlink_across_filesystems(
    tmp_path, models_dir, monkeypatch
):
    # os.link fails with EXDEV across filesystems and EPERM on a root-owned
    # file under fs.protected_hardlinks, which is Fedora's default -- so the
    # fallback is not a rare path, it is the ordinary one for /usr/share.
    def refuse(*_args):
        raise PermissionError("fs.protected_hardlinks")

    monkeypatch.setattr(models.os, "link", refuse)
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    target = models.adopt(source, models_dir)
    assert target.is_symlink()
    assert target.read_bytes() == source.read_bytes()


def test_a_symlinked_model_whose_original_is_gone_is_not_offered(
    tmp_path, models_dir, monkeypatch
):
    """A dead path must not reach the engine.

    `models_in` globs names, and a broken symlink still has a name. Handing
    that to whisper-server fails as "the engine did not start", which is the
    least legible error this program can produce -- while the real answer is
    the plainest one there is: the model is not there any more.
    """
    monkeypatch.setattr(models.os, "link", lambda *_: (_ for _ in ()).throw(OSError()))
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    models.adopt(source, models_dir)
    source.unlink()
    assert models.models_in(models_dir) == []


def test_adopt_verifies_a_recognised_model_against_the_published_checksum(
    tmp_path, models_dir
):
    # Right size, wrong bytes: exactly what a half-finished copy from
    # somewhere else looks like, and the one thing size alone cannot catch.
    base = models.CATALOG["base"]
    impostor = a_model_file(tmp_path / "ggml-base.bin", base.size)
    with pytest.raises(models.DownloadError):
        models.adopt(impostor, models_dir, base)
    assert models.models_in(models_dir) == []


def test_adopt_refuses_a_file_that_is_not_a_model_at_all(tmp_path, models_dir):
    (tmp_path / "notes.bin").write_bytes(b"just some bytes")
    with pytest.raises(models.DownloadError):
        models.adopt(tmp_path / "notes.bin", models_dir)


def test_adopt_reports_progress_while_it_checks(tmp_path, models_dir, monkeypatch):
    # A sha256 of 1.6 GB is not instant, and a progress bar that does not move
    # during it reads as a hang.
    size = 2 * models.CHUNK
    source = a_model_file(tmp_path / "ggml-x.bin", size)
    entry = models.Model(
        key="x", filename="ggml-x.bin", size=size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        summary="model.base.summary", needs_gpu=False,
    )
    seen: list[int] = []
    models.adopt(source, models_dir, entry, lambda done, total: seen.append(done))
    assert seen and seen[-1] == entry.size


def test_a_dead_link_does_not_make_the_model_unadoptable(
    tmp_path, models_dir, monkeypatch
):
    """The state this feature creates, and then has to survive.

    `adopt` links symbolically wherever `os.link` is refused; the original is
    deleted a week later; `models_in` correctly hides the dead link, so the
    wizard reopens for want of a model -- into the one action that would fix
    it. `exists()` follows the link, so the name reads as free while `os.link`
    refuses it as EEXIST, and the Try again button repeats that forever.
    """
    monkeypatch.setattr(models.os, "link", lambda *_: (_ for _ in ()).throw(OSError()))
    gone = a_model_file(tmp_path / "gone/ggml-medium.bin", 104)
    models.adopt(gone, models_dir)
    gone.unlink()

    again = a_model_file(tmp_path / "here/ggml-medium.bin", 104)
    target = models.adopt(again, models_dir)
    assert target.read_bytes() == again.read_bytes()
    assert models.models_in(models_dir) == [target]


def test_a_name_already_taken_is_never_returned_as_though_it_were_ours(
    tmp_path, models_dir
):
    """Returning the occupant would report a check that never ran.

    The wizard writes whatever comes back into `config["model"]` under a label
    saying "verified". An unrelated file that happens to share the name would
    be handed to the engine with that word attached to it.
    """
    (models_dir / "ggml-x.bin").write_bytes(models.GGML_MAGIC + b"something else")
    source = a_model_file(tmp_path / "ggml-x.bin", 104)

    target = models.adopt(source, models_dir)
    assert target != models_dir / "ggml-x.bin"
    assert target.read_bytes() == source.read_bytes()
    # And the file that was already there is untouched: this program does not
    # delete what it cannot identify.
    assert (models_dir / "ggml-x.bin").read_bytes() != source.read_bytes()


def test_a_broken_copy_of_a_known_model_is_replaced(tmp_path, models_dir):
    # A truncated download of the same model, under the same name. A finished
    # download replaces one of those, and so does this.
    base = models.CATALOG["base"]
    a_model_file(models_dir / base.filename, 14)

    source = a_model_file(tmp_path / "elsewhere/ggml-base.bin", base.size)
    entry = base._replace(sha256=_digest(source))
    target = models.adopt(source, models_dir, entry)
    assert target == models_dir / base.filename
    assert target.stat().st_size == base.size


def test_adopting_the_same_file_twice_is_not_a_second_model(tmp_path, models_dir):
    source = a_model_file(tmp_path / "ggml-medium.bin", 104)
    first = models.adopt(source, models_dir)
    assert models.adopt(source, models_dir) == first
    assert models.models_in(models_dir) == [first]
