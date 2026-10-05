"""Reusable microphone recording lifecycle for the CLI and desktop app."""

from __future__ import annotations

import math
import threading
from typing import Any


SILENCE_RMS_THRESHOLD = 1e-4


def _audio_modules() -> tuple[Any, Any]:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "microphone recording needs sounddevice and numpy; install with `uv sync`"
        ) from exc
    return np, sd


class Recorder:
    """Capture mono float32 audio from the default microphone.

    ``start`` opens the input stream, ``stop`` returns its samples and closes
    the stream, and ``cancel`` closes the stream while discarding its samples.
    The class never writes recordings to disk.
    """

    def __init__(self) -> None:
        self._stream: Any | None = None
        self._sample_rate: int | None = None
        self._audio_blocks: list[Any] = []
        self._callback_warnings: list[str] = []
        self._active = False
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def sample_rate(self) -> int | None:
        with self._lock:
            return self._sample_rate

    @property
    def warnings(self) -> tuple[str, ...]:
        """Return non-fatal sounddevice callback warnings from this capture."""
        with self._lock:
            return tuple(self._callback_warnings)

    def start(self) -> None:
        """Open and start microphone capture, raising if the device is unusable."""
        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("recording is already active")
            # A completed/cancelled instance can be reused for another capture.
            self._stream = None
            self._sample_rate = None
            self._audio_blocks = []
            self._callback_warnings = []

        np, sd = _audio_modules()
        del np  # NumPy is required by the callback and stop path, loaded lazily.
        try:
            device_info = sd.query_devices(kind="input")
        except Exception as exc:
            raise RuntimeError(f"could not find a microphone input device: {exc}") from exc

        if int(device_info.get("max_input_channels", 0)) < 1:
            raise RuntimeError("the default audio device has no microphone input channel")

        sample_rate = int(round(float(device_info["default_samplerate"])))
        if sample_rate <= 0:
            raise RuntimeError("the default microphone reported an invalid sample rate")

        def capture_callback(indata: Any, frame_count: int, time_info: Any, status: Any) -> None:
            del frame_count, time_info
            with self._lock:
                if status:
                    self._callback_warnings.append(str(status))
                if self._active:
                    # sounddevice reuses the input buffer after this callback.
                    self._audio_blocks.append(indata[:, 0].copy())

        try:
            stream = sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=capture_callback,
            )
        except Exception as exc:
            raise RuntimeError(f"could not open the microphone: {exc}") from exc

        with self._lock:
            self._stream = stream
            self._sample_rate = sample_rate
            self._active = True
        try:
            stream.start()
        except Exception as exc:
            self._release_stream()
            raise RuntimeError(f"microphone recording failed: {exc}") from exc

    def stop(self) -> tuple[Any, int]:
        """Stop capture, close the microphone, and return audio and sample rate."""
        with self._lifecycle_lock:
            return self._stop()

    def _stop(self) -> tuple[Any, int]:
        with self._lock:
            if not self._active or self._stream is None or self._sample_rate is None:
                raise RuntimeError("recording is not active")
            stream = self._stream

        release_error = self._release_stream(stream)
        if release_error is not None:
            raise RuntimeError(f"microphone recording failed: {release_error}") from release_error

        np, _ = _audio_modules()
        with self._lock:
            blocks = self._audio_blocks
            sample_rate = self._sample_rate
        if not blocks:
            raise RuntimeError("没有录到音频，请检查麦克风后重试。")

        audio = np.concatenate(blocks).astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        if not math.isfinite(rms) or rms < SILENCE_RMS_THRESHOLD:
            raise RuntimeError(
                "录音几乎没有声音，已跳过转写；请检查麦克风输入和系统权限。"
            )
        return audio, sample_rate

    def cancel(self) -> None:
        """Stop and close microphone capture, discarding any captured audio."""
        with self._lifecycle_lock:
            self._cancel()

    def _cancel(self) -> None:
        with self._lock:
            stream = self._stream
            if stream is None:
                self._active = False
                self._audio_blocks = []
                return
        release_error = self._release_stream(stream)
        with self._lock:
            self._audio_blocks = []
            self._callback_warnings = []
        if release_error is not None:
            raise RuntimeError(f"microphone recording failed: {release_error}") from release_error

    def _release_stream(self, stream: Any | None = None) -> Exception | None:
        """Release the current stream, attempting close even if stop fails."""
        with self._lock:
            current = stream if stream is not None else self._stream
            if current is None:
                self._active = False
                return None

        error: Exception | None = None
        try:
            if getattr(current, "active", False):
                current.stop()
        except Exception as exc:  # Always attempt close after a stop failure.
            error = exc
        try:
            current.close()
        except Exception as exc:
            if error is None:
                error = exc
        finally:
            with self._lock:
                self._active = False
                if current is self._stream:
                    self._stream = None
        return error
