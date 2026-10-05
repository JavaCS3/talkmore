import unittest
from concurrent.futures import Future

from talkmore.controller import DictationController


class ManualExecutor:
    """Deterministic executor: tests decide when each submitted task runs."""

    def __init__(self):
        self.futures = []

    def submit(self, task):
        future = Future()
        self.futures.append((task, future))
        return future

    def run(self, index=-1):
        task, future = self.futures[index]
        if future.done():
            return
        try:
            future.set_result(task())
        except BaseException as exc:
            future.set_exception(exc)


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeRecorder:
    def __init__(self, events=None, start_error=None, stop_error=None):
        self.events = events if events is not None else []
        self.start_error = start_error
        self.stop_error = stop_error
        self.active = False
        self.start_count = 0
        self.stop_count = 0
        self.cancel_count = 0

    def start(self):
        self.events.append("start")
        self.start_count += 1
        if self.start_error:
            raise self.start_error
        self.active = True

    def stop(self):
        self.events.append("stop")
        self.stop_count += 1
        if self.stop_error:
            raise self.stop_error
        self.active = False
        return ("audio", 16000)

    def cancel(self):
        self.events.append("cancel")
        self.cancel_count += 1
        self.active = False


class ControllerTests(unittest.TestCase):
    def make_controller(self, **overrides):
        executor = ManualExecutor()
        clock = overrides.pop("clock", FakeClock())
        events = []
        states = []
        recorders = []
        deliveries = []
        transcriptions = []
        load_calls = []

        def recorder_factory():
            recorder = overrides.get("recorder") or FakeRecorder(events)
            recorders.append(recorder)
            return recorder

        def load_model():
            load_calls.append(True)
            if "load_error" in overrides:
                raise overrides["load_error"]
            return "session"

        def transcribe(session, audio_pair):
            transcriptions.append((session, audio_pair))
            if "transcribe_error" in overrides:
                raise overrides["transcribe_error"]
            return overrides.get("text", "recognized words")

        def deliver(text, done):
            deliveries.append((text, done))
            if overrides.get("auto_deliver", False):
                done(overrides.get("delivery_status"))

        controller = DictationController(
            recorder_factory=recorder_factory,
            submit=executor.submit,
            dispatch=lambda callback: callback(),
            load_model=load_model,
            transcribe=transcribe,
            deliver=deliver,
            on_update=lambda state, message: states.append((state, message)),
            max_duration=overrides.get("max_duration", 300.0),
            clock=clock,
        )
        return (
            controller, executor, clock, events, states, recorders, deliveries,
            transcriptions, load_calls
        )

    def ready(self, controller, executor):
        executor.run(0)
        self.assertEqual(controller.state, DictationController.READY)

    def test_start_stop_transcribe_deliver_and_keep_latest_text_in_memory(self):
        (
            controller, executor, _, events, states, recorders, deliveries,
            transcriptions, load_calls
        ) = self.make_controller()
        self.assertEqual(controller.state, DictationController.PREPARING)
        self.ready(controller, executor)

        controller.toggle()
        self.assertEqual(controller.state, DictationController.RECORDING)
        self.assertEqual(events, ["start"])
        controller.toggle()
        self.assertEqual(controller.state, DictationController.TRANSCRIBING)
        self.assertEqual(events, ["start", "stop"])
        executor.run(1)

        self.assertEqual(transcriptions, [("session", ("audio", 16000))])
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(len(deliveries[0]), 2)
        self.assertEqual(deliveries[0][0], "recognized words")
        self.assertEqual(controller.latest_text, "recognized words")
        self.assertEqual(controller.state, DictationController.TRANSCRIBING)
        deliveries[0][1]("已插入文字")
        self.assertEqual(controller.state, DictationController.READY)
        self.assertEqual(controller.message, "已插入文字")
        self.assertEqual(len(load_calls), 1)
        self.assertEqual(recorders[0].stop_count, 1)
        self.assertTrue(any(state == DictationController.RECORDING for state, _ in states))

    def test_deadline_stops_once_and_later_toggle_cannot_submit_twice(self):
        clock = FakeClock()
        (
            controller, executor, _, _, _, recorders, deliveries,
            transcriptions, _
        ) = self.make_controller(clock=clock)
        self.ready(controller, executor)
        controller.toggle()
        clock.now = 299.99
        controller.tick()
        self.assertEqual(controller.state, DictationController.RECORDING)
        clock.now = 300.0
        controller.tick()
        controller.toggle()
        controller.tick()

        self.assertEqual(controller.state, DictationController.TRANSCRIBING)
        self.assertEqual(recorders[0].stop_count, 1)
        self.assertEqual(len(executor.futures), 2)
        executor.run(1)
        self.assertEqual(len(transcriptions), 1)
        self.assertEqual(len(deliveries), 1)
        self.assertIn("300 秒", controller.message)

    def test_cancel_recording_releases_recorder_without_transcribing(self):
        (
            controller, executor, _, events, _, recorders, deliveries,
            transcriptions, _
        ) = self.make_controller()
        self.ready(controller, executor)
        controller.toggle()
        controller.cancel()

        self.assertEqual(controller.state, DictationController.READY)
        self.assertEqual(controller.message, "已取消")
        self.assertEqual(recorders[0].cancel_count, 1)
        self.assertEqual(events, ["start", "cancel"])
        self.assertEqual(len(executor.futures), 1)
        self.assertEqual(transcriptions, [])
        self.assertEqual(deliveries, [])

    def test_cancel_during_transcription_ignores_stale_worker_output(self):
        (
            controller, executor, _, _, _, recorders, deliveries,
            transcriptions, _
        ) = self.make_controller()
        self.ready(controller, executor)
        controller.toggle()
        controller.toggle()
        controller.cancel()
        executor.run(1)

        self.assertEqual(controller.state, DictationController.READY)
        self.assertEqual(controller.message, "已取消")
        self.assertEqual(recorders[0].stop_count, 1)
        self.assertEqual(transcriptions, [("session", ("audio", 16000))])
        self.assertEqual(deliveries, [])
        self.assertIsNone(controller.latest_text)

    def test_load_failure_is_retryable_and_successful_session_is_loaded_once(self):
        load_attempts = iter([RuntimeError("model unavailable"), "session"])
        load_calls = []

        def load_model():
            load_calls.append(True)
            result = next(load_attempts)
            if isinstance(result, Exception):
                raise result
            return result

        executor = ManualExecutor()
        updates = []
        controller = DictationController(
            recorder_factory=FakeRecorder,
            submit=executor.submit,
            dispatch=lambda callback: callback(),
            load_model=load_model,
            transcribe=lambda session, audio: "ok",
            deliver=lambda text, done: done(),
            on_update=lambda state, message: updates.append((state, message)),
        )
        executor.run(0)
        self.assertEqual(controller.state, DictationController.ERROR)
        self.assertIn("model unavailable", controller.message)

        controller.retry()
        executor.run(1)
        self.assertEqual(controller.state, DictationController.READY)
        controller.toggle()
        controller.toggle()
        executor.run(2)
        self.assertEqual(load_calls, [True, True])
        self.assertEqual(controller.latest_text, "ok")
        self.assertTrue(any(state == DictationController.ERROR for state, _ in updates))

    def test_worker_failure_returns_to_ready_with_error_message(self):
        (
            controller, executor, _, _, _, _, deliveries, _, _
        ) = self.make_controller(transcribe_error=RuntimeError("ASR failed"))
        self.ready(controller, executor)
        controller.toggle()
        controller.toggle()
        executor.run(1)

        self.assertEqual(controller.state, DictationController.READY)
        self.assertIn("ASR failed", controller.message)
        self.assertEqual(deliveries, [])

    def test_close_during_transcription_ignores_later_completion(self):
        (
            controller, executor, _, events, _, recorders, deliveries, _, _
        ) = self.make_controller()
        self.ready(controller, executor)
        controller.toggle()
        controller.toggle()
        controller.close()
        executor.run(1)

        self.assertTrue(controller.closed)
        self.assertEqual(events, ["start", "stop"])
        self.assertEqual(recorders[0].cancel_count, 0)
        self.assertEqual(deliveries, [])
        self.assertEqual(controller.state, DictationController.TRANSCRIBING)

    def test_close_during_recording_releases_recorder(self):
        (
            controller, executor, _, events, _, recorders, deliveries, _, _
        ) = self.make_controller()
        self.ready(controller, executor)
        controller.toggle()
        controller.close()

        self.assertTrue(controller.closed)
        self.assertEqual(controller.state, DictationController.RECORDING)
        self.assertEqual(events, ["start", "cancel"])
        self.assertEqual(recorders[0].cancel_count, 1)
        self.assertEqual(len(executor.futures), 1)
        self.assertEqual(deliveries, [])

    def test_start_failure_releases_recorder_and_leaves_retryable_ready_state(self):
        recorder = FakeRecorder(start_error=RuntimeError("device unavailable"))
        (
            controller, executor, _, events, _, recorders, deliveries, _, _
        ) = self.make_controller(recorder=recorder)
        self.ready(controller, executor)
        controller.toggle()

        self.assertEqual(controller.state, DictationController.READY)
        self.assertIn("device unavailable", controller.message)
        self.assertEqual(recorder.events, ["start", "cancel"])
        self.assertEqual(recorders[0].cancel_count, 1)
        self.assertEqual(deliveries, [])


if __name__ == "__main__":
    unittest.main()
