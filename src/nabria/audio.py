"""Input devices, via wpctl.

Which microphone a take was captured from is the single most useful thing this
tool can tell you when nothing comes out: `pw-record` follows PipeWire's
default source, and that default has been found pinned to a card profile with
nothing wired to it -- capturing digital silence, indistinguishable from a
daemon that is simply broken. So the device is named on screen, it is
switchable without leaving the app, and it can be measured on demand.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import wave
from array import array
from pathlib import Path

from .recorder import LEVEL_WARMUP_FRAMES, RATE, SILENT_DBFS, dbfs

# `wpctl status` marks the default node with an asterisk before the id.
_SOURCE_LINE = re.compile(r"^\s*│?\s*(\*?)\s*(\d+)\.\s+(.*?)\s*\[vol:")


class AudioError(RuntimeError):
    pass


def _wpctl(*args: str, timeout: float = 5.0) -> str:
    if not shutil.which("wpctl"):
        raise AudioError("wpctl is not installed")
    try:
        result = subprocess.run(
            ["wpctl", *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioError(str(exc)) from exc
    if result.returncode != 0:
        raise AudioError(result.stderr.strip() or f"wpctl {' '.join(args)} failed")
    return result.stdout


def sources() -> list[dict]:
    """Audio capture devices as {id, name, default, muted, volume}.

    `wpctl status` groups by media type and repeats the "Sources:" heading for
    video, so the scan stops at the first blank-ish line after the audio block
    rather than matching every "Sources:" in the output.
    """
    lines = _wpctl("status").splitlines()
    found: list[dict] = []
    inside = False
    for line in lines:
        if "Sources:" in line:
            if inside:  # the Video section's own Sources heading
                break
            inside = True
            continue
        if not inside:
            continue
        match = _SOURCE_LINE.match(line)
        if not match:
            if found:  # blank line closes the audio source block
                break
            continue
        star, node_id, name = match.groups()
        entry = {"id": int(node_id), "name": name.strip(), "default": star == "*"}
        entry.update(_volume(entry["id"]))
        found.append(entry)
    return found


def _volume(node_id: int) -> dict:
    try:
        out = _wpctl("get-volume", str(node_id))
    except AudioError:
        return {"volume": 1.0, "muted": False}
    muted = "MUTED" in out
    try:
        volume = float(out.split("Volume:")[1].split()[0])
    except (IndexError, ValueError):
        volume = 1.0
    return {"volume": volume, "muted": muted}


def default_source() -> dict | None:
    for source in sources():
        if source["default"]:
            return source
    return None


def set_default(node_id: int) -> None:
    _wpctl("set-default", str(node_id))


def set_volume(node_id: int, volume: float) -> None:
    _wpctl("set-volume", str(node_id), f"{volume:.2f}")
    _wpctl("set-mute", str(node_id), "0")


def measure(seconds: float = 4.0) -> float:
    """Record from the default source and return its RMS in dBFS.

    Uses the same warm-up skip as a real take, so the number it reports is the
    number the silence gate will see -- a mic test that measured the device's
    open transient along with the room would read tens of dB high and call a
    dead microphone healthy.
    """
    with tempfile.TemporaryDirectory(prefix="nabria-mic-") as directory:
        path = Path(directory) / "test.wav"
        command = [
            "pw-record",
            "--rate", str(RATE),
            "--channels", "1",
            "--format", "s16",
            str(path),
        ]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except OSError as exc:
            raise AudioError(f"pw-record: {exc}") from exc
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if not path.exists():
            stderr = (process.stderr.read() if process.stderr else b"").decode(
                "utf-8", "replace"
            )
            raise AudioError(stderr.strip() or "pw-record produced no audio")
        return _rms_dbfs(path)


def _rms_dbfs(path: Path) -> float:
    """The take's average level, on exactly the scale the silence gate uses.

    Goes through `recorder.dbfs` rather than repeating 20*log10(x/32768):
    two definitions of this number mean the microphone test and the gate that
    actually throws takes away can disagree about the same recording, which is
    this project's most expensive misdiagnosis.

    The warm-up frames are skipped in the file rather than read and sliced off:
    the ALSA device-open pop dominates a short take's RMS, and seeking past it
    avoids materialising the samples only to discard them.
    """
    with wave.open(str(path)) as source:
        remaining = source.getnframes() - LEVEL_WARMUP_FRAMES
        if remaining <= 0:
            return SILENT_DBFS
        source.setpos(LEVEL_WARMUP_FRAMES)
        frames = source.readframes(remaining)
    samples = array("h")
    samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    if not samples:
        return SILENT_DBFS
    energy = 0.0
    for value in samples:
        energy += float(value) * float(value)
    return dbfs(math.sqrt(energy / len(samples)) / 32768.0)
