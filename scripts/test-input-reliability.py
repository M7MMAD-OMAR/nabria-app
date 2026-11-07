#!/usr/bin/env python3
"""Opt-in local PipeWire/Nemotron reliability gate with no owner-device changes.

Creates one owned null sink, records its monitor, and replaces injection only in
this process. Recognized text is a consistency reference, not human ground truth.
The stdout contract is one JSON test summary; private evidence goes to the path
in NABRIA_REPAIR_EVIDENCE. No audio or model request leaves localhost.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
DEFAULT_EVIDENCE = Path.home() / ".local/state/codex-runs/nabria-20261107/native"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def command(args, *, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stderr[-800:]}")
    return result.stdout.strip()


def device_snapshot():
    sources = json.loads(command(["pactl", "--format=json", "list", "sources"]))
    return {
        "default_source": command(["pactl", "get-default-source"]),
        "default_sink": command(["pactl", "get-default-sink"]),
        "sources": {row["name"]: {"mute": row["mute"], "volume": row["volume"]} for row in sources},
    }


def read_wav(path, np):
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != RATE:
            raise ValueError(f"Expected mono s16 at {RATE}: {path}")
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").copy()


def write_wav(path, values, np):
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(np.asarray(values, dtype="<i2").tobytes())


def rms_dbfs(values, np):
    if not len(values):
        return -120.0
    energy = float(np.mean(np.asarray(values, dtype=np.float64) ** 2))
    return 20 * math.log10(math.sqrt(energy) / 32768) if energy else -120.0


def scaled(values, level, np):
    factor = 10 ** ((level - rms_dbfs(values, np)) / 20)
    result = np.rint(np.asarray(values, dtype=np.float64) * factor)
    if float(np.max(np.abs(result))) > 32767:
        raise ValueError("Fixture gain would clip")
    return result.astype("<i2")


def correlation(reference, captured, np):
    """Align the graph's latency, then inspect full signal and final voiced PCM.

    PipeWire may resample its graph clock. Correlation is therefore used rather
    than claiming bit-identical capture at a hardware-independent sample offset.
    """
    x, y = reference.astype(np.float64), captured.astype(np.float64)
    if len(y) < len(x):
        raise AssertionError("Capture ended before the complete fixture")
    size = 1 << (len(x) + len(y) - 2).bit_length()
    cross = np.fft.irfft(np.fft.rfft(y, size) * np.conj(np.fft.rfft(x, size)), size)
    offset = int(np.argmax(cross[:len(y) - len(x) + 1]))
    aligned = y[offset:offset + len(x)]

    def score(a, b):
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denominator) if denominator else 1.0

    active = np.flatnonzero(np.abs(x) > max(2, float(np.max(np.abs(x))) * .01))
    if not len(active):
        return {"offsetFrames": offset, "whole": 1.0, "tail": 1.0}
    end = int(active[-1]) + 1
    start = max(0, end - RATE)
    result = {"offsetFrames": offset, "whole": score(x, aligned), "tail": score(x[start:end], aligned[start:end]),
              "referenceFrames": len(x), "capturedFrames": len(y), "lastVoicedFrame": end}
    assert result["whole"] > .97, result
    assert result["tail"] > .95, result
    assert len(y) - offset >= end, "Final voiced frames were not captured"
    return result


class Gate:
    def __init__(self, output, np, app, config, recorder, server):
        self.output, self.np, self.app, self.config = output, np, app, config
        self.Recorder, self.server = recorder, server
        self.results, self.deliveries, self.notices, self.owned_pids = [], [], [], []
        self.module = None
        self.sink = "nabria_native_" + uuid.uuid4().hex
        self.before = device_snapshot()
        self.initial_names = set(self.before["sources"])
        self.daemon = app.Daemon.__new__(app.Daemon)
        self.daemon.settings = dict(server.settings)
        self.daemon.settings.update(always_copy=True, keep_audio=False, silence_threshold_dbfs=-42,
                                    silent_notice_after=0, silence_warning_seconds=12)
        self.daemon.whisper = server
        self.daemon.recording = None
        self.daemon.live_stop = self.daemon.live_session = self.daemon.live_result = None
        self.daemon.jobs = 0
        self.daemon.jobs_lock = threading.Lock()
        self.daemon.silent_run = 0
        self.daemon.gpu_fallback_noticed = False
        self.daemon.orb = SimpleNamespace(state="working", hide=lambda: None, flash=lambda *_: None,
                                          set_transcript=lambda *_: None)
        self.daemon.log = self.log
        self.current_recorder = None

    def log(self, text):
        with (self.output / "events.log").open("a") as sink:
            sink.write(str(text) + "\n")

    def save(self):
        (self.output / "cases.json").write_text(json.dumps(self.results, ensure_ascii=False, indent=2))

    def case(self, name, function):
        record = {"name": name, "startedUtcEpoch": time.time()}
        self.results.append(record)
        try:
            record["evidence"] = function()
            record["passed"] = True
        except Exception:
            record.update(passed=False, error=traceback.format_exc())
            self.log(record["error"])
        record["wallSeconds"] = time.time() - record["startedUtcEpoch"]
        self.save()

    def open_audio(self):
        self.module = command(["pactl", "load-module", "module-null-sink", "sink_name=" + self.sink,
                               "rate=16000", "channels=1"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.sink in command(["pactl", "list", "short", "sinks"]):
                break
            time.sleep(.05)
        assert command(["pactl", "get-default-source"]) == self.before["default_source"]
        assert command(["pactl", "get-default-sink"]) == self.before["default_sink"]

    def capture(self, name, pcm, *, wait_for_warning=False, speech_alignment=True):
        path = self.output / (name + "-play.wav")
        write_wav(path, pcm, self.np)
        recorder = self.Recorder(self.output / (name + "-take.wav"))
        self.current_recorder = recorder
        recorder.start()
        time.sleep(.35)
        player = subprocess.Popen(["pw-play", "--target", self.sink, str(path)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, error = player.communicate(timeout=len(pcm) / RATE + 15)
            assert player.returncode == 0, error.decode(errors="replace")
            time.sleep(.25)
            if wait_for_warning:
                assert recorder.seconds > 12, recorder.seconds
                assert recorder.unheard(-42, 12)[0] is False, "Pause diluted a real take into a silence warning"
        finally:
            if player.poll() is None:
                player.kill()
                player.wait()
            recorder.stop()
            self.current_recorder = None
        retained = self.output / (name + "-captured.wav")
        shutil.copyfile(recorder.destination, retained)
        metrics = {"wholeRmsDbfs": recorder.rms_dbfs, "activityDbfs": recorder.activity_dbfs,
                   "maxPeakDbfs": recorder.max_peak_dbfs, "frames": recorder.total_frames,
                   "wav": str(retained), "sha256": digest(retained)}
        if speech_alignment and self.np.max(self.np.abs(pcm.astype(self.np.int32))) > 100:
            metrics["pcmAlignment"] = correlation(pcm, read_wav(retained, self.np), self.np)
        return recorder, metrics

    def transcribe(self, recorder):
        before = len(self.deliveries)
        self.daemon._transcribe(recorder)
        return self.deliveries[before:]

    def no_speech(self, name, pcm, *, nonzero):
        # A resampled impulse spreads into neighboring frames, so speech
        # correlation is inappropriate. The capture and decision still run.
        recorder, metrics = self.capture(name, pcm, speech_alignment=False)
        assert recorder.activity_dbfs <= -42, metrics
        previous = set(self.app.FAILED_DIR.glob("quiet-*.wav"))
        # Any invocation of inference here would spend time hallucinating over
        # known nonspeech. Preserve it for recovery without typing model output.
        with patch.object(self.server, "transcribe", side_effect=AssertionError("Nonspeech sent to ASR")):
            assert self.transcribe(recorder) == []
        if nonzero:
            retained = set(self.app.FAILED_DIR.glob("quiet-*.wav")) - previous
            assert len(retained) == 1, "This ambiguous nonzero take was not retained"
            saved = retained.pop()
            assert digest(saved) == metrics["sha256"], "Retained audio differs from the captured take"
            metrics["retainedWav"] = str(saved)
        return metrics

    def close(self):
        if self.current_recorder is not None:
            self.current_recorder.stop()
        if self.server.process is not None:
            self.owned_pids.append(self.server.process.pid)
        self.server.stop()
        if self.module is not None:
            command(["pactl", "unload-module", self.module])
            self.module = None
        after = device_snapshot()
        # Ignore the temporary monitor which has just been removed. Never
        # restore a changed owner setting, because that could undo a new choice.
        preserved = {key: after[key] == self.before[key] for key in ["default_sink", "default_source"]}
        preserved["original_source_mute_volume"] = all(after["sources"].get(name) == self.before["sources"][name]
                                                         for name in self.initial_names)
        alive = []
        for pid in set(self.owned_pids):
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                if state not in {"Z", "X"}:
                    alive.append(pid)
            except OSError:
                pass
        (self.output / "cleanup.json").write_text(json.dumps({"preserved": preserved, "owned_engine_pids_alive": alive}, indent=2))
        assert all(preserved.values()), preserved
        assert not alive, alive


def retained_reference(output, np, server):
    override = os.environ.get("NABRIA_TEST_WAV")
    audio_directory = os.environ.get("NABRIA_TEST_AUDIO_DIR")
    if not override and not audio_directory:
        raise FileNotFoundError("Set NABRIA_TEST_WAV or NABRIA_TEST_AUDIO_DIR to retained local speech")
    candidates = [Path(override)] if override else sorted(Path(audio_directory).glob("*.wav"))[:180]
    if not candidates:
        raise FileNotFoundError("Retained local speech is required; set NABRIA_TEST_WAV")
    ranked = []
    for path in candidates:
        try:
            with wave.open(str(path), "rb") as source:
                if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                    continue
                duration = source.getnframes() / source.getframerate()
                if not 1 <= duration <= 15:
                    continue
                pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
            peak = int(np.max(np.abs(pcm.astype(np.int32))))
            rms = rms_dbfs(pcm, np)
            if rms > -65 and peak > 50:
                ranked.append((rms, path))
        except (OSError, ValueError, wave.Error):
            pass
    for number, (_, path) in enumerate(sorted(ranked, key=lambda row: row[0], reverse=True)[:8]):
        decoded = output / f"retained-candidate-{number}-16k.wav"
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(path),
                 "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(decoded)])
        pcm = read_wav(decoded, np)
        reference = output / "retained-reference.wav"
        body = scaled(pcm, -28, np)
        write_wav(reference, body, np)
        text = server.transcribe(reference)
        if len(text.strip()) >= 5:
            return body, {"original": str(path), "originalSha256": digest(path), "normalizedWav": str(reference),
                          "recognizedText": text, "note": "Recognition consistency reference, not human ground truth."}
    raise AssertionError("No retained speech candidate produced a usable local reference")


def run(output):
    import numpy as np

    # Load layer-shell before GTK/Wayland, matching the normal launcher.
    import ctypes
    import ctypes.util
    layer = ctypes.util.find_library("gtk4-layer-shell")
    if layer:
        ctypes.CDLL(layer, mode=ctypes.RTLD_GLOBAL)
    sys.path.insert(0, str(ROOT / "src"))
    owner_configuration = Path.home() / ".config/nabria/config.json"
    owner_hash = digest(owner_configuration)
    settings = json.loads(owner_configuration.read_text())
    for variable, directory in [("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")]:
        os.environ[variable] = str(output / "profile" / directory)
    from nabria import app, config
    from nabria.recorder import Recorder
    from nabria.nemotron import NemotronServer

    if not hasattr(Recorder, "activity_dbfs"):
        raise RuntimeError("Recorder.activity_dbfs repair is not installed")
    settings.update(server_port=0, idle_unload_seconds=0, prewarm=False, corrections={})
    settings["model"] = str(Path.home() / ".local/share/nabria/models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
    settings["nemo_binary"] = str(Path.home() / ".local/libexec/nabria/nemo-speech/bin/nemo-speech")
    model = Path(settings["model"])
    binary = Path(settings["nemo_binary"])
    for tool in ["pactl", "pw-record", "pw-play", "ffmpeg"]:
        if not shutil.which(tool):
            raise FileNotFoundError(tool)
    for required in [model, binary]:
        if not required.is_file():
            raise FileNotFoundError(required)
    server = NemotronServer(settings, lambda text: gate.log(text))
    gate = Gate(output, np, app, config, Recorder, server)
    original_popen = subprocess.Popen
    guard = output / "owned-parent-guard.py"
    guard.write_text("import ctypes,os,signal,sys\nctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0)\n"
                     "if os.getppid()!=int(sys.argv[1]):raise SystemExit(125)\nos.execvpe(sys.argv[2],sys.argv[2:],os.environ)\n")

    def popen(args, *positional, **kwargs):
        if isinstance(args, (list, tuple)) and args:
            args = list(args)
            if Path(str(args[0])).name == "pw-record":
                args[1:1] = ["--target", gate.sink, "--properties", "stream.capture.sink=true"]
            elif str(args[0]) == str(binary):
                # Harness-owned cleanup survives an interrupted test runner.
                # This safety wrapper is not a claim about daemon parent death.
                args = [sys.executable, str(guard), str(os.getpid()), *args]
            child = original_popen(args, *positional, **kwargs)
            if str(binary) in args:
                gate.owned_pids.append(child.pid)
            return child
        return original_popen(args, *positional, **kwargs)

    cleanup_error = None
    try:
        gate.open_audio()
        provenance = {"model": str(model), "modelSha256": digest(model), "engine": str(binary),
                      "engineSha256": digest(binary), "sourceHashes": {str(p.relative_to(ROOT)): digest(p)
                      for p in [Path(__file__).resolve(), ROOT / "src/nabria/recorder.py", ROOT / "src/nabria/app.py",
                                ROOT / "src/nabria/nemotron.py", ROOT / "src/nabria/whisper.py"]},
                      "ownerConfigSha256Before": owner_hash, "scope": "Linux native PipeWire plus local Nemotron; no native Windows or WER claim."}
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("subprocess.Popen", popen))
            stack.enter_context(patch.object(app.GLib, "idle_add", lambda callback, *args: callback(*args)))
            stack.enter_context(patch.object(app.inject, "to_clipboard", lambda text: None))
            stack.enter_context(patch.object(app.inject, "deliver", lambda text, *_args, **_kw: gate.deliveries.append(text) or "isolated-test"))
            stack.enter_context(patch.object(app.notify, "send", lambda *args, **kw: gate.notices.append({"args": args, "kwargs": kw})))
            stack.enter_context(patch.object(app, "_default_input", lambda: (gate.sink + ".monitor", False)))
            body, reference = retained_reference(output, np, server)
            gate.case("retained_local_voice_reference", lambda: reference)

            def consistent(text):
                normalize = lambda value: re.sub(r"[\W_]+", "", value.casefold())
                ratio = difflib.SequenceMatcher(None, normalize(reference["recognizedText"]), normalize(text)).ratio()
                assert ratio >= .6, f"Same-model gain consistency below 0.6: {ratio}: {text!r}"
                return ratio

            zeros = lambda seconds: np.zeros(round(seconds * RATE), dtype="<i2")
            quiet = scaled(np.concatenate([zeros(1), body, zeros(.5)]), -45, np)

            def quiet_case():
                take, metrics = gate.capture("quiet-voice", quiet)
                assert take.rms_dbfs <= -42 and take.activity_dbfs > -42, metrics
                delivered = gate.transcribe(take)
                assert len(delivered) == 1 and delivered[0].strip(), "Quiet speech was discarded"
                metrics["deliveredText"] = delivered[0]
                metrics["referenceTextSimilarity"] = consistent(delivered[0])
                return metrics
            gate.case("quiet_voice_below_average_gate", quiet_case)

            def pause_case():
                pcm = scaled(np.concatenate([zeros(1), body, zeros(13)]), -44.5, np)
                take, metrics = gate.capture("voice-pause", pcm, wait_for_warning=True)
                assert take.rms_dbfs <= -42 and take.activity_dbfs > -42, metrics
                delivered = gate.transcribe(take)
                assert len(delivered) == 1, "Pause-diluted voice was discarded"
                metrics["deliveredText"] = delivered[0]
                metrics["referenceTextSimilarity"] = consistent(delivered[0])
                return metrics
            gate.case("speech_then_long_pause", pause_case)
            gate.case("digital_silence_no_inference_or_paste", lambda: gate.no_speech("silence", zeros(3), nonzero=False))
            click = zeros(3)
            click[2 * RATE] = 29000
            gate.case("isolated_click_not_speech", lambda: gate.no_speech("click", click, nonzero=True))
            noise = np.random.default_rng(7).choice(np.array([-70, 70], dtype="<i2"), size=3 * RATE)
            gate.case("low_stationary_noise_not_speech", lambda: gate.no_speech("noise", noise, nonzero=True))

            def repeat_case():
                captures = []
                for number in range(3):
                    take, metrics = gate.capture(f"repeat-{number}", quiet)
                    delivered = gate.transcribe(take)
                    assert len(delivered) == 1
                    metrics["referenceTextSimilarity"] = consistent(delivered[0])
                    assert take.process.poll() is not None
                    assert take.reader is None or not take.reader.is_alive()
                    captures.append(metrics)
                return {"captures": captures, "takeCount": len(captures)}
            gate.case("repeated_start_stop_keeps_final_voiced_frames", repeat_case)

            def crash_case():
                server.ensure()
                old = server.process
                assert old is not None and old.poll() is None
                old_args = Path(f"/proc/{old.pid}/cmdline").read_bytes().split(b"\0")
                assert old_args[0] == os.fsencode(binary) and os.fsencode(str(model)) in old_args
                sent = threading.Event()
                original_send = http.client.HTTPConnection.send
                old.send_signal(signal.SIGSTOP)

                def send(connection, data):
                    result = original_send(connection, data)
                    if connection.port == server.port and isinstance(data, bytes) and b"Content-Disposition" in data and not sent.is_set():
                        sent.set()
                        old.kill()
                    return result
                try:
                    with patch.object(http.client.HTTPConnection, "send", send):
                        text = server.transcribe(output / "retained-reference.wav")
                    assert sent.is_set(), "Engine fault was not actually injected after sending request audio"
                    assert text.strip(), "Recovery produced no text"
                    assert server.process is not None and server.process.pid != old.pid
                    assert old.poll() is not None
                    return {"killedEnginePid": old.pid, "replacementPid": server.process.pid, "recoveredText": text,
                            "gpuAbandoned": server.gpu_failed, "fault": "SIGKILL after real request body was sent to owned stopped engine"}
                finally:
                    if old.poll() is None:
                        old.send_signal(signal.SIGCONT)
            gate.case("owned_engine_death_retries_saved_audio", crash_case)
    except BaseException:
        gate.results.append({"name": "setup_or_execution", "passed": False, "error": traceback.format_exc()})
        gate.save()
    finally:
        try:
            gate.close()
            assert digest(owner_configuration) == owner_hash, "Owner configuration changed"
        except Exception:
            cleanup_error = traceback.format_exc()
            gate.results.append({"name": "cleanup_and_owner_preservation", "passed": False, "error": cleanup_error})
        gate.save()
    return {"total": len(gate.results), "failed": sum(not row["passed"] for row in gate.results), "skipped": 0}


def main():
    base = Path(os.environ.get("NABRIA_REPAIR_EVIDENCE", DEFAULT_EVIDENCE))
    output = base / ("run-" + uuid.uuid4().hex)
    output.mkdir(parents=True)
    original_stdout = os.dup(1)
    with (output / "native.log").open("w") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            result = run(output)
        except BaseException:
            (output / "setup-error.txt").write_text(traceback.format_exc())
            result = {"total": 1, "failed": 1, "skipped": 0}
        (base / "latest-report.json").write_text(json.dumps({**result, "output": str(output)}, indent=2))
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(original_stdout, 1)
        os.close(original_stdout)
    print(json.dumps(result), flush=True)
    return int(result["failed"] != 0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
