"""WASAPI capture, feeding the shared PCM statistics and silence gate."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

from .. import config
from ..recorder import CHANNELS, RATE, Recorder as PCMRecorder, MissingRecorder


def _sd():
    import sounddevice
    return sounddevice


def sources() -> list[dict]:
    from ..audio import AudioError
    try:
        sd = _sd()
        saved = config.load().get("input_device")
        default = sd.default.device[0]
        return [
            {"id": index, "name": device["name"],
             "default": device["name"] == saved if saved else index == default,
             "muted": None, "volume": 1.0}
            for index, device in enumerate(sd.query_devices())
            if device["max_input_channels"] > 0
            and sd.query_hostapis(device["hostapi"])["name"] == "Windows WASAPI"
        ]
    except Exception as exc:
        raise AudioError(str(exc)) from exc


def set_default(node_id: int) -> None:
    from ..audio import AudioError
    source = next((item for item in sources() if item["id"] == node_id), None)
    if source is None:
        raise AudioError("The selected microphone is no longer connected")
    settings = config.load()
    settings["input_device"] = source["name"]
    config.save(settings)


class Recorder(PCMRecorder):
    def __init__(self, destination: Path):
        super().__init__(destination)
        self.stream = None

    def start(self) -> None:
        try:
            sd = _sd()
            devices = sources()
            chosen = next((item for item in devices if item["default"]), None)
            if chosen is None:
                # PortAudio's default may name its MME view of a WASAPI
                # endpoint. Resolve its name before choosing another device.
                name = sd.query_devices(kind="input")["name"]
                chosen = next((item for item in devices if item["name"] == name), None)
            if chosen is None:
                raise MissingRecorder("No WASAPI microphone is available")
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            self.stream = sd.RawInputStream(
                samplerate=RATE, channels=CHANNELS, dtype="int16",
                device=chosen["id"], blocksize=1024,
                extra_settings=sd.WasapiSettings(auto_convert=True),
            )
            self.stream.start()
        except Exception as exc:
            if self.stream is not None:
                self.stream.close()
            raise MissingRecorder(str(exc)) from exc
        self.reader = threading.Thread(target=self._capture, daemon=True, name="nabria-wasapi")
        self.reader.start()

    def _capture(self) -> None:
        try:
            with wave.open(str(self.destination), "wb") as sink:
                sink.setnchannels(CHANNELS)
                sink.setsampwidth(2)
                sink.setframerate(RATE)
                while not self.stop_event.is_set():
                    data, overflow = self.stream.read(1024)
                    if overflow:
                        self.error = "Microphone capture overflowed; the audio may be incomplete"
                    data = bytes(data)
                    sink.writeframes(data)
                    self._observe(data)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = str(exc)
        finally:
            self.stream.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self.reader:
            self.reader.join(timeout=3)
            if self.reader.is_alive():
                self.stream.abort()
                self.reader.join(timeout=3)
            if self.reader.is_alive():
                raise RuntimeError("Microphone capture did not stop")


def measure(seconds: float = 4.0) -> float:
    import tempfile
    from ..audio import AudioError
    try:
        with tempfile.TemporaryDirectory(prefix="nabria-mic-") as directory:
            recorder = Recorder(Path(directory) / "test.wav")
            recorder.start()
            try:
                time.sleep(seconds)
            finally:
                recorder.stop()
            if recorder.error:
                raise AudioError(recorder.error)
            return recorder.rms_dbfs
    except Exception as exc:
        raise AudioError(str(exc)) from exc
