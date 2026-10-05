"""Command-line interface for recording and transcribing speech locally."""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

from talkmore.models import DEFAULT_MODEL, ensure_model, resolve_model

DEFAULT_MAX_DURATION = 120.0
SILENCE_RMS_THRESHOLD = 1e-4


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def _load_session(model: str) -> tuple[Any, Any, float]:
    """Resolve the model through the download-size guard, then load MLX lazily."""
    model = resolve_model(model)
    print(f"当前模型：{model}", file=sys.stderr)
    model_path = ensure_model(model)
    try:
        import mlx.core as mx
        from mlx_qwen3_asr import Session
    except ImportError as exc:
        raise RuntimeError(
            f"无法加载 ASR 运行时：{exc}。请确认 uv sync 已完成，"
            "并在能访问 Metal GPU 的 Apple Silicon macOS 终端运行。"
        ) from exc

    reset_peak_memory = getattr(mx, "reset_peak_memory", None)
    if callable(reset_peak_memory):
        reset_peak_memory()
    started_at = time.perf_counter()
    session = Session(model=str(model_path))
    return session, mx, time.perf_counter() - started_at


def _language_option(language: str) -> str | None:
    return None if language.lower() == "auto" else language


def _write_wav(path: Path, audio: Any, sample_rate: int) -> None:
    """Save mono float audio as a broadly compatible 16-bit PCM WAV."""
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2", copy=False)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def _record_audio(max_duration: float) -> tuple[Any, int]:
    """Record mono float32 audio until Enter is pressed or the limit is reached."""
    if not sys.stdin.isatty():
        raise RuntimeError("record needs an interactive terminal so Enter can start and stop it")

    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "microphone recording needs sounddevice and numpy; install with `uv sync`"
        ) from exc

    try:
        device_info = sd.query_devices(kind="input")
    except Exception as exc:
        raise RuntimeError(f"could not find a microphone input device: {exc}") from exc

    if int(device_info.get("max_input_channels", 0)) < 1:
        raise RuntimeError("the default audio device has no microphone input channel")

    sample_rate = int(round(float(device_info["default_samplerate"])))
    if sample_rate <= 0:
        raise RuntimeError("the default microphone reported an invalid sample rate")

    audio_blocks: list[Any] = []
    callback_warnings: list[str] = []

    def capture_callback(indata: Any, frame_count: int, time_info: Any, status: Any) -> None:
        del frame_count, time_info
        if status:
            callback_warnings.append(str(status))
        # The callback's buffer is reused by sounddevice, so retain a copy.
        audio_blocks.append(indata[:, 0].copy())

    print(f"Using the default microphone at {sample_rate} Hz.", file=sys.stderr)
    print("按 Enter 开始录音…", file=sys.stderr, flush=True)
    try:
        input()
    except EOFError as exc:
        raise RuntimeError("record needs an interactive terminal") from exc

    try:
        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=capture_callback,
        )
    except Exception as exc:
        raise RuntimeError(f"could not open the microphone: {exc}") from exc
    stop_requested = threading.Event()

    def wait_for_enter() -> None:
        try:
            print("录音中，按 Enter 停止…", file=sys.stderr, flush=True)
            input()
        except EOFError:
            pass
        finally:
            stop_requested.set()

    reader = threading.Thread(target=wait_for_enter, name="talkmore-enter", daemon=True)
    started_at = time.monotonic()
    timed_out = False
    stream_started = False
    try:
        stream.start()
        stream_started = True
        reader.start()
        while not stop_requested.wait(timeout=0.1):
            if time.monotonic() - started_at >= max_duration:
                timed_out = True
                break
    except KeyboardInterrupt:
        print("\n录音已取消。", file=sys.stderr)
        raise
    except Exception as exc:
        raise RuntimeError(f"microphone recording failed: {exc}") from exc
    finally:
        # Stop and close the audio device even on Ctrl+C or an input error.
        try:
            if stream_started and stream.active:
                stream.stop()
        finally:
            stream.close()

    if timed_out:
        print(f"已达到 {max_duration:g} 秒录音上限。", file=sys.stderr)
    if callback_warnings:
        print(f"Audio input warning: {callback_warnings[0]}", file=sys.stderr)

    if not audio_blocks:
        raise RuntimeError("没有录到音频，请检查麦克风后重试。")

    audio = np.concatenate(audio_blocks).astype(np.float32, copy=False)
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    if not math.isfinite(rms) or rms < SILENCE_RMS_THRESHOLD:
        raise RuntimeError(
            "录音几乎没有声音，已跳过转写；请检查麦克风输入和系统权限。"
        )
    return audio, sample_rate


def _transcribe(session: Any, audio: Any, language: str | None) -> tuple[str, float]:
    started_at = time.perf_counter()
    result = session.transcribe(audio, language=language)
    return str(result.text).strip(), time.perf_counter() - started_at


def _print_runtime_stats(mx: Any, load_seconds: float, inference_seconds: float) -> None:
    import resource

    print(f"Model load: {load_seconds:.2f}s", file=sys.stderr)
    print(f"Inference: {inference_seconds:.2f}s", file=sys.stderr)
    if sys.platform == "darwin":
        rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        print(f"Process peak RSS: {rss_mib:.0f} MiB", file=sys.stderr)
    try:
        synchronize = getattr(mx, "synchronize", None)
        if callable(synchronize):
            synchronize()
        peak_memory = getattr(mx, "get_peak_memory", None)
        if callable(peak_memory):
            peak_mib = float(peak_memory()) / (1024 * 1024)
            print(f"MLX peak memory: {peak_mib:.0f} MiB", file=sys.stderr)
    except Exception:
        # Diagnostics must not turn a successful transcription into a failure.
        pass


def _copy_to_clipboard(text: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("剪贴板复制目前仅支持 macOS 的 pbcopy")
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"无法复制到剪贴板：{exc}") from exc


def _add_transcription_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"4bit / 8bit, Hugging Face model ID, or local model directory (default: 8bit, {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--language",
        default="auto",
        help="spoken language, or 'auto' for detection (default: auto)",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="copy the transcript to the macOS clipboard",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talkmore",
        description="Record speech and transcribe it locally with Qwen3-ASR.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    record_parser = commands.add_parser("record", help="record from the default microphone")
    _add_transcription_options(record_parser)
    record_parser.add_argument(
        "--max-duration",
        type=_positive_seconds,
        default=DEFAULT_MAX_DURATION,
        metavar="SECONDS",
        help=f"maximum recording duration (default: {DEFAULT_MAX_DURATION:g})",
    )
    record_parser.add_argument(
        "--save-audio",
        type=Path,
        metavar="WAV_PATH",
        help="also save the recording as a mono 16-bit PCM WAV file",
    )

    transcribe_parser = commands.add_parser(
        "transcribe", help="transcribe an existing audio file"
    )
    _add_transcription_options(transcribe_parser)
    transcribe_parser.add_argument("audio", type=Path, help="audio file to transcribe")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "record":
            audio, sample_rate = _record_audio(args.max_duration)
            if args.save_audio is not None:
                _write_wav(args.save_audio, audio, sample_rate)
                print(f"录音已保存：{args.save_audio}", file=sys.stderr)
            print("正在准备本地模型并转写…", file=sys.stderr)
            session, mx, load_seconds = _load_session(args.model)
            transcript, inference_seconds = _transcribe(
                session, (audio, sample_rate), _language_option(args.language)
            )
        else:
            if not args.audio.is_file():
                raise RuntimeError(f"音频文件不存在：{args.audio}")
            print("正在准备本地模型并转写…", file=sys.stderr)
            session, mx, load_seconds = _load_session(args.model)
            transcript, inference_seconds = _transcribe(
                session, str(args.audio), _language_option(args.language)
            )

        _print_runtime_stats(mx, load_seconds, inference_seconds)

        if not transcript:
            raise RuntimeError("模型没有返回可显示的文字。")

        print(transcript)
        if args.copy:
            _copy_to_clipboard(transcript)
            print("已复制到剪贴板。", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"talkmore: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
