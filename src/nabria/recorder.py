"""Microphone capture through PipeWire.

pw-record writes raw s16 to stdout and this module frames it into a WAV file
itself, rather than letting pw-record write the file. Owning the byte stream is
what makes the live level ring and the silence guard possible -- both need the
samples as they arrive, not after the take is finished.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import threading
import wave
from array import array
from collections import deque
from pathlib import Path

RATE = 16_000  # whisper resamples anything else, so feed it its native rate
CHANNELS = 1
CHUNK_BYTES = 8 * 1024
SILENT_DBFS = -120.0
# Opening the ALSA capture device pops: the first fraction of a second comes
# back tens of dB above the room, loud enough on its own to carry a take of
# pure silence past the RMS gate and hand whisper noise to hallucinate over.
# Those frames are still written to the WAV -- the pop is harmless to
# transcription and cutting it would risk clipping the first syllable -- but
# they are left out of the level statistics the gate reads. Measured at 0.4 s
# on this laptop's internal mic; the margin covers a slower device.
#
# Exactly this many frames are skipped, trimmed inside the chunk that crosses
# the boundary. Dropping whole chunks instead would round up to 0.768 s, which
# also raised the "too short to measure" cutoff well past what it claimed.
LEVEL_WARMUP_FRAMES = int(0.6 * RATE)


class MissingRecorder(RuntimeError):
    """PipeWire is not installed, which is not a bug to be traced back."""


def _levels(data: bytes) -> tuple[float, int, float]:
    """Peak in dBFS, sample count, and summed energy for the RMS average."""
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return SILENT_DBFS, 0, 0.0
    peak = max(abs(value) for value in samples)
    energy = 0.0
    for value in samples:
        energy += float(value) * float(value)
    if peak <= 0:
        return SILENT_DBFS, len(samples), 0.0
    return 20.0 * math.log10(peak / 32768.0), len(samples), energy


def dbfs(ratio: float) -> float:
    if ratio <= 0:
        return SILENT_DBFS
    return 20.0 * math.log10(ratio)


class Recorder:
    """One dictation take. Not reusable -- construct a new one each time."""

    def __init__(self, destination: Path):
        self.destination = Path(destination)
        self.process: subprocess.Popen[bytes] | None = None
        self.reader: threading.Thread | None = None
        self.stderr_reader: threading.Thread | None = None
        # pw-record keeps writing warnings to stderr for as long as it runs and
        # the pipe only holds 64 KiB. An undrained pipe would eventually block
        # the recorder mid-write, so this thread keeps it empty.
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.peak_dbfs = SILENT_DBFS
        self.max_peak_dbfs = SILENT_DBFS
        self.energy = 0.0
        self.samples = 0
        self.total_frames = 0
        self.error = ""
        # Set by the daemon when it has already warned, mid-take, that nothing
        # is arriving. It rides on the take rather than on the daemon because
        # takes overlap -- a new one starts while the last is still being
        # transcribed -- so a single flag on the daemon would describe the
        # wrong recording. Read at the far end to keep the finished-take
        # notice from repeating what the user was already told.
        self.warned_unheard = False

    @property
    def seconds(self) -> float:
        with self.lock:
            return self.total_frames / RATE

    @property
    def measured(self) -> bool:
        """Whether any audio outlived the warm-up and reached the statistics.

        False means the take ended inside the device-open transient, so
        `rms_dbfs` is the SILENT_DBFS placeholder rather than a measurement.
        A caller that treats that as evidence about the microphone is reading
        a number nobody took.
        """
        with self.lock:
            return self.samples > 0

    @property
    def rms_dbfs(self) -> float:
        """Average level over the whole take.

        The guard uses this rather than the peak because a single keyboard
        click or chair creak pushes the peak well above any silence threshold,
        while the take as a whole is still nothing but room tone -- which is
        exactly what whisper turns into a confident "شكرا للمشاهدة".
        """
        with self.lock:
            return self._rms_dbfs()

    def _rms_dbfs(self) -> float:
        """`rms_dbfs` with the lock already held.

        `self.lock` is a plain Lock, not a reentrant one, so anything that
        holds it cannot go back through the property -- and a second copy of
        this arithmetic is exactly how the microphone test and the gate that
        throws takes away came to disagree about one recording.
        """
        if self.samples == 0:
            return SILENT_DBFS
        return dbfs(math.sqrt(self.energy / self.samples) / 32768.0)

    def unheard(self, threshold: float, after: float) -> bool:
        """Whether the take has run `after` seconds without hearing anything.

        Read while the recording is still going, which is the whole point:
        the silent-take notice can only speak once a take is finished, and by
        then the sentence has already been said into a muted microphone. This
        is the same judgement made in time to do something about it.

        Both readings come from one acquisition of the lock. Taken separately
        they can straddle a chunk arriving, which is how a length measured
        before it and a level measured after it end up describing different
        recordings.

        It cannot fire inside the warm-up however small `after` is set:
        `samples` stays zero until the device-open transient is past, and a
        take with no measured level is not evidence about the microphone.
        """
        if after <= 0:
            return False
        with self.lock:
            if self.samples == 0 or self.total_frames < after * RATE:
                return False
            return self._rms_dbfs() <= threshold

    def start(self) -> None:
        # Checked by name rather than left to Popen, whose FileNotFoundError
        # arrives as a traceback in the log and a generic failure on screen.
        # A machine without PipeWire cannot record at all, and saying so is
        # worth more than any amount of stack.
        if not shutil.which("pw-record"):
            # Package names live in scripts/install.sh, which is the thing
            # that can actually check them against a distribution. This said
            # "pipewire" for Arch while the installer said "pipewire-audio";
            # only one of them is right, and it is not the one that cannot be
            # tested. So point at the installer rather than guess again.
            raise MissingRecorder(
                "pw-record was not found. Nabria records through PipeWire -- "
                "install it with your package manager, or re-run "
                "scripts/install.sh, which names the package for your system."
            )
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "pw-record",
            "--rate", str(RATE),
            "--channels", str(CHANNELS),
            "--format", "s16",
            "--raw",
            "-",
        ]
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.reader = threading.Thread(target=self._read, daemon=True, name="nabria-capture")
        self.reader.start()
        self.stderr_reader = threading.Thread(
            target=self._drain_stderr, daemon=True, name="nabria-capture-stderr"
        )
        self.stderr_reader.start()

    def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            with wave.open(str(self.destination), "wb") as sink:
                sink.setnchannels(CHANNELS)
                sink.setsampwidth(2)
                sink.setframerate(RATE)
                while not self.stop_event.is_set():
                    data = self.process.stdout.read(CHUNK_BYTES)
                    if not data:
                        break
                    sink.writeframes(data)
                    frames = len(data) // 2
                    live, _, _ = _levels(data)
                    with self.lock:
                        start = self.total_frames
                        self.total_frames += frames
                        # The live meter is never gated on the warm-up. The orb
                        # reads this, and holding it at SILENT_DBFS through the
                        # opening of a take would draw a healthy microphone
                        # exactly like a dead one for the first three quarters
                        # of a second -- which is when the user is watching to
                        # see whether it heard them.
                        self.peak_dbfs = live
                    if start + frames <= LEVEL_WARMUP_FRAMES:
                        continue
                    if start < LEVEL_WARMUP_FRAMES:
                        # Trim within the chunk rather than dropping it whole:
                        # chunks are 0.256 s, so discarding at chunk
                        # granularity threw away 0.768 s, not the 0.6 s meant.
                        data = data[(LEVEL_WARMUP_FRAMES - start) * 2 :]
                    level, count, energy = _levels(data)
                    with self.lock:
                        self.max_peak_dbfs = max(self.max_peak_dbfs, level)
                        self.energy += energy
                        self.samples += count
        except Exception as exc:  # noqa: BLE001 - surfaced in the orb, never raised into GTK
            with self.lock:
                self.error = str(exc)

    def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.stderr_tail.append(line.decode("utf-8", "replace"))

    def stop(self) -> None:
        """Stop capture and return once the WAV on disk is complete."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.stop_event.set()
        if self.reader:
            # The writer thread closes the WAV header on exit; joining here is
            # what guarantees the file is readable by the time we POST it.
            self.reader.join(timeout=5)

    def recent_stderr(self) -> str:
        return "".join(self.stderr_tail)[-800:]
