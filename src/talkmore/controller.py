"""Injectable lifecycle controller for global dictation.

The controller deliberately knows nothing about AppKit, audio backends, or the
ASR runtime. Its public methods are intended to be called on the UI thread;
worker completions are marshalled back through ``dispatch`` before changing
state or delivering text.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, Protocol


class Recorder(Protocol):
    """Small recording interface required by :class:`DictationController`."""

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> Any: ...

    def cancel(self) -> None: ...


class DictationController:
    """Coordinate model startup, recording, transcription, and delivery.

    ``submit`` must accept a zero-argument callable and return a Future-like
    object implementing ``add_done_callback`` and ``result`` (for example a
    ``concurrent.futures.Executor``'s ``submit`` method). ``deliver`` receives
    the recognized text and a completion callback. The delivery layer owns
    clipboard and system paste behavior; the controller does not inspect or
    retain input focus targets.
    """

    PREPARING = "preparing"
    READY = "ready"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"

    def __init__(
        self,
        recorder_factory: Callable[[], Recorder],
        submit: Callable[[Callable[[], Any]], Any],
        dispatch: Callable[[Callable[[], None]], None],
        load_model: Callable[[], Any],
        transcribe: Callable[[Any, Any], str],
        deliver: Callable[[str, Callable[[str | None], None]], None],
        on_update: Callable[[str, str], None],
        max_duration: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_duration <= 0:
            raise ValueError("max_duration must be greater than zero")
        self._recorder_factory = recorder_factory
        self._submit = submit
        self._dispatch = dispatch
        self._load_model = load_model
        self._transcribe = transcribe
        self._deliver = deliver
        self._on_update = on_update
        self._max_duration = float(max_duration)
        self._clock = clock

        self.state = self.PREPARING
        self.message = ""
        self.latest_text: str | None = None
        self.closed = False
        self._session: Any = None
        self._recorder: Recorder | None = None
        self._started_at: float | None = None
        self._generation = 0
        self._set_state(self.PREPARING, "正在加载语音模型…")
        self._load_session_async()

    @property
    def elapsed(self) -> float:
        """Current recording duration, or zero when no recording is active."""
        if self.state != self.RECORDING or self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def retry(self) -> None:
        """Retry model initialization after a startup failure."""
        if self.closed or self.state != self.ERROR or self._session is not None:
            return
        self._set_state(self.PREPARING, "正在重试加载语音模型…")
        self._load_session_async()

    def toggle(self) -> None:
        """Start recording from ready, or stop the current recording."""
        if self.closed:
            return
        if self.state == self.READY:
            self._start_recording()
        elif self.state == self.RECORDING:
            self._stop_recording()

    def tick(self) -> None:
        """Update time-driven behavior; call regularly from the UI loop."""
        if self.state == self.RECORDING and self.elapsed >= self._max_duration:
            self._stop_recording(
                message=f"已达到 {self._max_duration:g} 秒录音上限，正在转写…"
            )

    def cancel(self) -> None:
        """Cancel recording or invalidate an in-flight transcription result."""
        if self.closed or self.state not in (self.RECORDING, self.TRANSCRIBING):
            return
        self._generation += 1
        recorder, self._recorder = self._recorder, None
        self._started_at = None
        error: Exception | None = None
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception as exc:  # still restore the controller lifecycle
                error = exc
        if error is not None:
            self._set_state(self.READY, f"取消录音时发生错误：{error}")
        else:
            self._set_state(self.READY, "已取消")

    def close(self) -> None:
        """Stop an active recorder and ignore all later asynchronous results."""
        if self.closed:
            return
        self.closed = True
        self._generation += 1
        recorder, self._recorder = self._recorder, None
        self._started_at = None
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception as exc:
                # Shutdown must finish even if the audio backend is already gone.
                self.message = f"关闭录音设备时发生错误：{exc}"

    def _load_session_async(self) -> None:
        self._run_async(self._load_model, self._model_loaded)

    def _model_loaded(self, value: Any = None, error: Exception | None = None) -> None:
        if self.closed:
            return
        if error is not None:
            self._set_state(self.ERROR, f"语音模型加载失败：{error}")
            return
        self._session = value
        self._set_state(self.READY, "就绪")

    def _start_recording(self) -> None:
        self._generation += 1
        generation = self._generation
        try:
            recorder = self._recorder_factory()
            self._recorder = recorder
            recorder.start()
            self._started_at = self._clock()
        except Exception as exc:
            recorder, self._recorder = self._recorder, None
            self._started_at = None
            if recorder is not None:
                try:
                    recorder.cancel()
                except Exception:
                    pass
            self._set_state(self.READY, f"录音启动失败：{exc}")
            return
        if generation == self._generation and not self.closed:
            self._set_state(self.RECORDING, "正在录音")

    def _stop_recording(self, message: str = "正在转写…") -> None:
        if self.closed or self.state != self.RECORDING:
            return
        # Change state before touching the backend. This makes a simultaneous
        # hotkey/timer event idempotent even if stop() takes time.
        self._set_state(self.TRANSCRIBING, message)
        recorder, self._recorder = self._recorder, None
        self._started_at = None
        generation = self._generation
        try:
            if recorder is None:
                raise RuntimeError("录音设备不可用")
            audio_pair = recorder.stop()
        except Exception as exc:
            if recorder is not None:
                try:
                    recorder.cancel()
                except Exception:
                    pass
            self._set_state(self.READY, f"录音停止失败：{exc}")
            return

        def transcribe_task() -> str:
            return self._transcribe(self._session, audio_pair)

        def transcription_done(text: Any = None, error: Exception | None = None) -> None:
            if self.closed or generation != self._generation:
                return
            if error is not None:
                self._set_state(self.READY, f"转写失败：{error}")
                return
            recognized = str(text).strip()
            if not recognized:
                self._set_state(self.READY, "没有识别到文字，请重试。")
                return
            self.latest_text = recognized
            completed = False

            def delivered(status: str | None = None) -> None:
                nonlocal completed

                def finish_delivery() -> None:
                    nonlocal completed
                    if completed or self.closed or generation != self._generation:
                        return
                    completed = True
                    self._set_state(self.READY, status or "已完成")

                # A delivery adapter may complete from a timer or another
                # worker; keep all controller and UI state changes on main.
                self._dispatch(finish_delivery)

            try:
                self._deliver(recognized, delivered)
            except Exception as exc:
                if not completed and not self.closed and generation == self._generation:
                    completed = True
                    self._set_state(self.READY, f"文字交付失败：{exc}")

        self._run_async(transcribe_task, transcription_done)

    def _run_async(
        self,
        task: Callable[[], Any],
        completion: Callable[[Any, Exception | None], None],
    ) -> None:
        try:
            future = self._submit(task)
        except Exception as exc:
            self._dispatch(lambda exc=exc: completion(None, exc))
            return

        def finished(done_future: Future[Any]) -> None:
            try:
                value = done_future.result()
                error = None
            except Exception as exc:  # Future stores worker exceptions here.
                value, error = None, exc
            self._dispatch(
                lambda value=value, error=error: completion(value, error)
            )

        try:
            future.add_done_callback(finished)
        except Exception as exc:
            self._dispatch(lambda exc=exc: completion(None, exc))

    def _set_state(self, state: str, message: str) -> None:
        self.state = state
        self.message = message
        self._on_update(state, message)
