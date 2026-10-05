import unittest
from unittest.mock import patch

import numpy as np
from talkmore.cli import _record_audio


class FakeStream:
    def __init__(self, callback, fail=False):
        self.callback = callback
        self.fail = fail
        self.active = False
        self.closed = False

    def start(self):
        if self.fail:
            raise RuntimeError("device disconnected")
        self.active = True
        self.callback(np.full((100, 1), 0.1, dtype=np.float32), 100, None, None)

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True


class RecordingLifecycleTests(unittest.TestCase):
    def record(self, fail=False):
        streams = []

        def create(**kwargs):
            stream = FakeStream(kwargs["callback"], fail)
            streams.append(stream)
            return stream

        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""), patch("sounddevice.query_devices", return_value={"max_input_channels": 1, "default_samplerate": 48000}), patch("sounddevice.InputStream", side_effect=create):
            try:
                return _record_audio(1), streams
            finally:
                if streams:
                    self.assertTrue(streams[0].closed)
                    self.assertFalse(streams[0].active)

    def test_enter_stop_returns_copied_audio_and_closes_stream(self):
        (audio, rate), _ = self.record()
        self.assertEqual(rate, 48000)
        self.assertEqual(audio.shape, (100,))
        np.testing.assert_allclose(audio, 0.1)

    def test_start_failure_closes_stream(self):
        with self.assertRaisesRegex(RuntimeError, "device disconnected"):
            self.record(fail=True)

    def test_noninteractive_record_fails_before_opening_device(self):
        with patch("sys.stdin.isatty", return_value=False), patch("sounddevice.InputStream") as stream:
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                _record_audio(1)
            stream.assert_not_called()
