"""Capture levels and the warm-up.

The warm-up is the subtlest invariant in the project. The device-open pop is
about -32 dBFS and lasts a fraction of a second; left in the statistics it
carried takes of pure silence past the RMS gate, and whisper hallucinated a
polite sentence into the document. But the *live* meter must not be gated on
it, or the indicator draws a working microphone exactly like a dead one for the
first three quarters of a second -- which is precisely when the user is looking
at it.

So the two readings disagree by design, and these tests pin that down.
"""

from __future__ import annotations

import math
import struct

from nabria import recorder
from nabria.recorder import LEVEL_WARMUP_FRAMES, RATE, SILENT_DBFS, Recorder


class FakeStdout:
    """Hands out a byte stream in fixed-size reads, like a pipe."""

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int) -> bytes:
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def pcm(samples) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def drive(tmp_path, data: bytes) -> Recorder:
    """Run Recorder._read over a canned stream, with no pw-record involved."""
    take = Recorder(tmp_path / "take.wav")

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout(data)

    take.process = FakeProcess()  # type: ignore[assignment]
    take._read()
    return take


def test_warmup_is_exactly_point_six_seconds(tmp_path):
    # A loud pop for exactly the warm-up, then silence. If a single popped
    # frame leaked into the statistics the RMS would be far above silence.
    pop = [20_000] * LEVEL_WARMUP_FRAMES
    quiet = [0] * RATE
    take = drive(tmp_path, pcm(pop + quiet))

    assert take.total_frames == LEVEL_WARMUP_FRAMES + RATE
    assert take.samples == RATE  # every warm-up frame excluded, none over
    assert take.rms_dbfs == SILENT_DBFS


def test_warmup_is_trimmed_inside_the_chunk_that_crosses_it(tmp_path):
    # Chunks are 0.256s. Dropping whole chunks would discard 0.768s instead of
    # 0.600s, silently moving the boundary this whole module is built around.
    total = LEVEL_WARMUP_FRAMES + 1000
    take = drive(tmp_path, pcm([0] * total))
    assert take.samples == 1000


def test_live_meter_is_not_gated_on_the_warmup(tmp_path):
    # One chunk only, entirely inside the warm-up: nothing reaches the
    # statistics, but the orb must still have been given a real level.
    frames = recorder.CHUNK_BYTES // 2
    assert frames < LEVEL_WARMUP_FRAMES
    take = drive(tmp_path, pcm([16_384] * frames))

    assert take.samples == 0
    assert take.measured is False
    assert take.rms_dbfs == SILENT_DBFS      # nobody took this measurement
    assert take.peak_dbfs > -12.0            # but the meter saw the sound


def test_a_take_shorter_than_the_warmup_is_not_evidence(tmp_path):
    # It is neither silence nor a working microphone. app.py branches on
    # `measured` before the silence gate precisely so a stray double-press
    # cannot accuse a healthy input.
    take = drive(tmp_path, pcm([0] * 100))
    assert take.measured is False
    assert take.total_frames == 100


def test_rms_is_computed_over_the_whole_take_not_the_peak(tmp_path):
    # A single click in an otherwise silent take: the peak clears any
    # threshold, the average does not. The gate reads the average, or a room
    # with a chair creak in it becomes a transcript.
    click = [30_000]
    quiet = [0] * (2 * RATE)
    take = drive(tmp_path, pcm([0] * LEVEL_WARMUP_FRAMES + click + quiet))

    assert take.max_peak_dbfs > -2.0
    assert take.rms_dbfs < -42.0


def test_known_amplitude_gives_the_expected_rms(tmp_path):
    # Full-scale square wave: RMS == peak == 0 dBFS, so the arithmetic is
    # checkable against a number worked out by hand rather than a golden value.
    body = [32_767 if index % 2 else -32_767 for index in range(RATE)]
    take = drive(tmp_path, pcm([0] * LEVEL_WARMUP_FRAMES + body))
    assert math.isclose(take.rms_dbfs, 0.0, abs_tol=0.01)


def test_the_wav_keeps_the_warmup_that_the_statistics_drop(tmp_path):
    # The pop is excluded from the levels but must stay in the audio: cutting
    # it would risk clipping the first syllable, and it does no harm to
    # transcription.
    import wave

    total = LEVEL_WARMUP_FRAMES + RATE
    take = drive(tmp_path, pcm([1000] * total))
    with wave.open(str(take.destination)) as sink:
        assert sink.getnframes() == total
        assert sink.getframerate() == RATE
        assert sink.getnchannels() == 1


def test_odd_byte_counts_do_not_crash_the_reader(tmp_path):
    # A pipe read can split a 16-bit sample down the middle.
    take = drive(tmp_path, pcm([1000] * 5000)[:-1])
    assert take.error == ""
