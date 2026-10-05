import sys
import unittest
from unittest.mock import Mock, patch

if sys.platform == 'darwin':
    import Quartz as Q
    from talkmore.native_app import Application, RecordingPanel
    from talkmore.hotkeys import HotkeyConfig, HotkeyStateMachine


@unittest.skipUnless(sys.platform == 'darwin', 'macOS bridge')
class NativeEventTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.alloc().init()
        self.app.machine = HotkeyStateMachine(HotkeyConfig.right_option())
        self.app.hotkey = HotkeyConfig.right_option()
        self.app.settings_window = None
        self.app.captured_keys = set()
        self.app.controller = Mock()
        self.app.delivery = Mock()
        self.app.state = 'ready'

    def event(self, kind, key, flags=0):
        event = Q.CGEventCreateKeyboardEvent(None, key, kind != Q.kCGEventKeyUp)
        Q.CGEventSetType(event, kind)
        Q.CGEventSetFlags(event, flags)
        return self.app.handle_event(None, kind, event, None), event

    def test_native_right_option_release_dispatches_toggle_once(self):
        with patch('talkmore.native_app.AppHelper.callAfter') as dispatch:
            result, event = self.event(Q.kCGEventFlagsChanged, 61, Q.kCGEventFlagMaskAlternate)
            self.assertIs(result, event)
            dispatch.assert_not_called()
            self.event(Q.kCGEventFlagsChanged, 61)
            self.assertEqual(dispatch.call_count, 1)

    def test_native_chord_consumes_down_and_release(self):
        self.app.machine = HotkeyStateMachine(HotkeyConfig.chord(49, ['control']))
        with patch('talkmore.native_app.AppHelper.callAfter') as dispatch:
            result, _ = self.event(Q.kCGEventKeyDown, 49, Q.kCGEventFlagMaskControl)
            self.assertIsNone(result)
            result, _ = self.event(Q.kCGEventKeyUp, 49)
            self.assertIsNone(result)
            self.assertEqual(dispatch.call_count, 1)

    def test_escape_cancels_recording_without_dispatching_toggle(self):
        self.app.state = 'recording'
        with patch('talkmore.native_app.AppHelper.callAfter') as dispatch:
            result, _ = self.event(Q.kCGEventKeyDown, 53)
            self.assertIsNone(result)
            self.app.controller.cancel.assert_called_once()
            dispatch.assert_not_called()

    def test_recording_panel_never_accepts_key_or_main_focus(self):
        panel = RecordingPanel.alloc().init()
        self.assertFalse(panel.canBecomeKeyWindow())
        self.assertFalse(panel.canBecomeMainWindow())
        self.assertTrue(panel.accessibilityIsIgnored())

    def test_hotkey_starts_without_querying_or_activating_an_application(self):
        self.app.closed = False
        with patch('talkmore.native_app.macos.permissions', return_value={'ready':True}), patch('talkmore.native_app.A.NSWorkspace') as workspace, patch('talkmore.native_app.AppHelper.callLater') as later:
            self.app.toggle()
            self.app.controller.toggle.assert_called_once()
            workspace.assert_not_called()
            later.assert_not_called()
