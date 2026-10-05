import unittest

from talkmore.hotkeys import (
    FLAGS_CHANGED,
    KEY_DOWN,
    KEY_UP,
    MODIFIER_FLAG_MASKS,
    TOGGLE_RECORDING,
    HotkeyConfig,
    HotkeyStateMachine,
)


class RightOptionTests(unittest.TestCase):
    def setUp(self):
        self.machine = HotkeyStateMachine()

    def test_right_option_tap_toggles_only_on_release(self):
        down = self.machine.handle_event(FLAGS_CHANGED, 61, MODIFIER_FLAG_MASKS["option"])
        self.assertIsNone(down.action)
        up = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertEqual(up.action, TOGGLE_RECORDING)

    def test_left_option_does_not_toggle(self):
        self.machine.handle_event(FLAGS_CHANGED, 58, MODIFIER_FLAG_MASKS["option"])
        result = self.machine.handle_event(FLAGS_CHANGED, 58, 0)
        self.assertIsNone(result.action)

    def test_release_after_reset_does_not_create_a_phantom_press(self):
        option = MODIFIER_FLAG_MASKS["option"]
        first_release = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertIsNone(first_release.action)
        self.machine.handle_event(FLAGS_CHANGED, 61, option)
        actual_release = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertEqual(actual_release.action, TOGGLE_RECORDING)

    def test_right_option_used_with_another_key_does_not_toggle(self):
        self.machine.handle_event(FLAGS_CHANGED, 61, MODIFIER_FLAG_MASKS["option"])
        self.machine.handle_event(KEY_DOWN, 12, MODIFIER_FLAG_MASKS["option"])
        self.machine.handle_event(KEY_UP, 12)
        result = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertIsNone(result.action)

    def test_right_option_used_with_another_modifier_does_not_toggle(self):
        option = MODIFIER_FLAG_MASKS["option"]
        shift = MODIFIER_FLAG_MASKS["shift"]
        self.machine.handle_event(FLAGS_CHANGED, 61, option)
        self.machine.handle_event(FLAGS_CHANGED, 56, option | shift)
        self.machine.handle_event(FLAGS_CHANGED, 56, option)
        result = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertIsNone(result.action)

    def test_right_option_with_left_option_held_does_not_toggle(self):
        option = MODIFIER_FLAG_MASKS["option"]
        self.machine.handle_event(FLAGS_CHANGED, 58, option)
        self.machine.handle_event(FLAGS_CHANGED, 61, option)
        result = self.machine.handle_event(FLAGS_CHANGED, 61, option)
        self.assertIsNone(result.action)

    def test_key_held_before_right_option_makes_press_a_chord(self):
        option = MODIFIER_FLAG_MASKS["option"]
        self.machine.handle_event(KEY_DOWN, 12)
        self.machine.handle_event(FLAGS_CHANGED, 61, option)
        self.machine.handle_event(KEY_UP, 12)
        result = self.machine.handle_event(FLAGS_CHANGED, 61, 0)
        self.assertIsNone(result.action)


class ChordTests(unittest.TestCase):
    def setUp(self):
        config = HotkeyConfig.chord(12, {"command", "shift"})
        self.machine = HotkeyStateMachine(config)
        self.flags = MODIFIER_FLAG_MASKS["command"] | MODIFIER_FLAG_MASKS["shift"]

    def test_matching_chord_triggers_and_consumes_down_and_up(self):
        down = self.machine.handle_event(KEY_DOWN, 12, self.flags)
        self.assertTrue(down.consume)
        self.assertEqual(down.action, TOGGLE_RECORDING)
        up = self.machine.handle_event(KEY_UP, 12)
        self.assertTrue(up.consume)
        self.assertIsNone(up.action)

    def test_repeated_chord_key_is_consumed_without_retriggering(self):
        self.machine.handle_event(KEY_DOWN, 12, self.flags)
        repeat = self.machine.handle_event(KEY_DOWN, 12, self.flags, autorepeat=True)
        self.assertTrue(repeat.consume)
        self.assertIsNone(repeat.action)
        self.assertTrue(self.machine.handle_event(KEY_UP, 12).consume)
        next_press = self.machine.handle_event(KEY_DOWN, 12, self.flags)
        self.assertEqual(next_press.action, TOGGLE_RECORDING)

    def test_repeat_remains_consumed_after_modifiers_are_released(self):
        self.machine.handle_event(KEY_DOWN, 12, self.flags)
        repeat = self.machine.handle_event(KEY_DOWN, 12, 0, autorepeat=True)
        self.assertTrue(repeat.consume)
        self.assertIsNone(repeat.action)
        key_up = self.machine.handle_event(KEY_UP, 12)
        self.assertTrue(key_up.consume)

    def test_wrong_or_extra_modifiers_do_not_match(self):
        wrong = self.machine.handle_event(KEY_DOWN, 12, MODIFIER_FLAG_MASKS["command"])
        self.assertFalse(wrong.consume)
        extra_flags = self.flags | MODIFIER_FLAG_MASKS["option"]
        extra = self.machine.handle_event(KEY_DOWN, 12, extra_flags)
        self.assertFalse(extra.consume)

    def test_non_chord_keys_are_never_consumed(self):
        result = self.machine.handle_event(KEY_DOWN, 0, self.flags)
        self.assertFalse(result.consume)
        self.assertIsNone(result.action)

    def test_chords_require_a_modifier_and_ordinary_key(self):
        with self.assertRaises(ValueError):
            HotkeyConfig.chord(12, set())
        with self.assertRaises(ValueError):
            HotkeyConfig.chord(61, {"command"})


if __name__ == "__main__":
    unittest.main()
