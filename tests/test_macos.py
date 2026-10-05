"""Construct real CGEvents but never send keyboard input to a live application."""
import sys
import unittest
from unittest.mock import patch

if sys.platform == 'darwin':
    from talkmore import macos
    import Quartz as Q


@unittest.skipUnless(sys.platform == 'darwin', 'macOS adapter')
class PasteTests(unittest.TestCase):
    def setUp(self):
        # Sandboxed tests cannot allocate a private HID source. Construct real
        # event payloads with the default source; no events are ever posted.
        create = Q.CGEventCreateKeyboardEvent
        source = patch.object(macos.Q, 'CGEventSourceCreate', return_value=object())
        event = patch.object(macos.Q, 'CGEventCreateKeyboardEvent', side_effect=lambda ignored, key, down: create(None, key, down))
        source.start()
        self.addCleanup(source.stop)
        event.start()
        self.addCleanup(event.stop)

    def test_posts_complete_command_v_sequence_without_ax_element_calls(self):
        with patch.object(macos.AX, 'AXIsProcessTrusted', return_value=True), patch.object(macos.AX, 'AXUIElementCreateApplication', side_effect=AssertionError('no focus lookup')), patch.object(macos.AX, 'AXUIElementSetAttributeValue', side_effect=AssertionError('no AX text writes')), patch.object(macos.Q, 'CGEventPost') as post:
            macos.paste()
        events = [args.args[1] for args in post.call_args_list]
        keys = [Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode) for event in events]
        flags = [Q.CGEventGetFlags(event) for event in events]
        self.assertEqual(keys, [55, 9, 9, 55])
        self.assertEqual(flags, [Q.kCGEventFlagMaskCommand] * 3 + [0])
        self.assertEqual([Q.CGEventGetType(e) for e in events[1:3]], [Q.kCGEventKeyDown, Q.kCGEventKeyUp])
        self.assertTrue(all(args.args[0] == Q.kCGHIDEventTap for args in post.call_args_list))
        self.assertTrue(all(Q.CGEventGetIntegerValueField(e, Q.kCGEventSourceUserData) == 0x54414C4B for e in events))

    def test_permission_failure_does_not_send_any_keys(self):
        with patch.object(macos.AX, 'AXIsProcessTrusted', return_value=False), patch.object(macos.Q, 'CGEventPost') as post:
            with self.assertRaises(RuntimeError):
                macos.paste()
            post.assert_not_called()

    def test_constructs_all_events_before_posting(self):
        with patch.object(macos.AX, 'AXIsProcessTrusted', return_value=True), patch.object(macos.Q, 'CGEventCreateKeyboardEvent', return_value=None), patch.object(macos.Q, 'CGEventPost') as post:
            with self.assertRaises(RuntimeError):
                macos.paste()
            post.assert_not_called()

    def test_post_failure_releases_keys_without_repeating_v_down(self):
        calls = []
        def post(location, event):
            calls.append(event)
            if len(calls) == 3:
                raise RuntimeError('send failure')
        with patch.object(macos.AX, 'AXIsProcessTrusted', return_value=True), patch.object(macos.Q, 'CGEventPost', side_effect=post):
            with self.assertRaises(RuntimeError):
                macos.paste()
        v_downs = [e for e in calls if Q.CGEventGetType(e) == Q.kCGEventKeyDown and Q.CGEventGetIntegerValueField(e, Q.kCGKeyboardEventKeycode) == 9]
        self.assertEqual(len(v_downs), 1)
        self.assertEqual(Q.CGEventGetFlags(calls[-1]), 0)
