import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from talkmore.cli import DEFAULT_MAX_DURATION, _build_parser, _record_audio, main
from talkmore.recording import Recorder


class FakeStream:
    def __init__(self, callback, *, fail_start=False, fail_stop=False, audio=0.1):
        self.callback = callback
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.audio = audio
        self.active = False
        self.closed = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("device disconnected")
        self.active = True
        if self.audio is not None:
            self.callback(
                np.full((100, 1), self.audio, dtype=np.float32), 100, None, None
            )

    def stop(self):
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self.active = False

    def close(self):
        self.active = False
        self.closed = True


class RecorderLifecycleTests(unittest.TestCase):
    def recorder(self, *, fail_start=False, fail_stop=False, audio=0.1):
        streams = []

        def create(**kwargs):
            stream = FakeStream(
                kwargs["callback"],
                fail_start=fail_start,
                fail_stop=fail_stop,
                audio=audio,
            )
            streams.append(stream)
            return stream

        patches = (
            patch(
                "sounddevice.query_devices",
                return_value={"max_input_channels": 1, "default_samplerate": 48000},
            ),
            patch("sounddevice.InputStream", side_effect=create),
        )
        return streams, patches

    def test_start_stop_returns_float_audio_and_releases_device(self):
        streams, patches = self.recorder()
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            self.assertTrue(recorder.active)
            self.assertEqual(recorder.sample_rate, 48000)

            audio, rate = recorder.stop()

        self.assertEqual(rate, 48000)
        self.assertEqual(audio.shape, (100,))
        self.assertEqual(audio.dtype, np.float32)
        np.testing.assert_allclose(audio, 0.1)
        self.assertFalse(recorder.active)
        self.assertTrue(streams[0].closed)

    def test_cancel_releases_device_and_discards_audio(self):
        streams, patches = self.recorder()
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            recorder.cancel()

            self.assertFalse(recorder.active)
            self.assertTrue(streams[0].closed)
            with self.assertRaisesRegex(RuntimeError, "not active"):
                recorder.stop()

    def test_stream_start_failure_closes_device_and_clears_active_state(self):
        streams, patches = self.recorder(fail_start=True)
        with patches[0], patches[1]:
            recorder = Recorder()
            with self.assertRaisesRegex(RuntimeError, "device disconnected"):
                recorder.start()

        self.assertFalse(recorder.active)
        self.assertTrue(streams[0].closed)

    def test_stop_failure_still_closes_device_and_clears_active_state(self):
        streams, patches = self.recorder(fail_stop=True)
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                recorder.stop()

        self.assertFalse(recorder.active)
        self.assertTrue(streams[0].closed)

    def test_cancel_failure_still_closes_device_and_discards_audio(self):
        streams, patches = self.recorder(fail_stop=True)
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                recorder.cancel()

        self.assertFalse(recorder.active)
        self.assertTrue(streams[0].closed)
        self.assertEqual(recorder.warnings, ())

    def test_silent_recording_is_released_and_rejected(self):
        streams, patches = self.recorder(audio=0.0)
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            with self.assertRaisesRegex(RuntimeError, "几乎没有声音"):
                recorder.stop()

        self.assertTrue(streams[0].closed)
        self.assertFalse(recorder.active)

    def test_repeated_start_is_rejected_without_opening_another_stream(self):
        streams, patches = self.recorder()
        with patches[0], patches[1]:
            recorder = Recorder()
            recorder.start()
            with self.assertRaisesRegex(RuntimeError, "already active"):
                recorder.start()
            recorder.cancel()

        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].closed)


class CliRecordingCompatibilityTests(unittest.TestCase):
    def test_enter_stop_keeps_interactive_cli_behavior(self):
        streams = []

        def create(**kwargs):
            stream = FakeStream(kwargs["callback"])
            streams.append(stream)
            return stream

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value=""),
            patch(
                "sounddevice.query_devices",
                return_value={"max_input_channels": 1, "default_samplerate": 48000},
            ),
            patch("sounddevice.InputStream", side_effect=create),
        ):
            audio, rate = _record_audio(1)

        self.assertEqual(rate, 48000)
        self.assertEqual(audio.shape, (100,))
        np.testing.assert_allclose(audio, 0.1)
        self.assertTrue(streams[0].closed)
        self.assertFalse(streams[0].active)

    def test_noninteractive_cli_fails_before_opening_device(self):
        with patch("sys.stdin.isatty", return_value=False), patch(
            "sounddevice.InputStream"
        ) as stream:
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                _record_audio(1)
            stream.assert_not_called()

    def test_default_duration_is_five_minutes_for_record_and_app(self):
        self.assertEqual(DEFAULT_MAX_DURATION, 300)
        parser = _build_parser()
        self.assertEqual(parser.parse_args(["record"]).max_duration, 300)
        self.assertEqual(parser.parse_args(["app"]).max_duration, 300)

    def test_app_command_lazily_calls_run_app_with_options(self):
        calls = []
        fake_app = SimpleNamespace(
            run_app=lambda **kwargs: calls.append(kwargs) or 0
        )
        with patch.dict(sys.modules, {"talkmore.app": fake_app}):
            result = main(
                ["app", "--model", "4bit", "--language", "zh", "--max-duration", "42"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [{"model": "4bit", "language": "zh", "max_duration": 42.0}],
        )


if __name__ == "__main__":
    unittest.main()
