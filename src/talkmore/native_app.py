"""AppKit presentation and global event tap. Imported only by `talkmore app`."""
import concurrent.futures
import time
import objc
import AppKit as A
import Quartz as Q
from PyObjCTools import AppHelper
from talkmore import macos
from talkmore.config import load_config, save_config
from talkmore.hotkeys import HotkeyConfig, HotkeyStateMachine, KEY_DOWN, KEY_UP, FLAGS_CHANGED
from talkmore.recording import Recorder
from talkmore.controller import DictationController
from talkmore.delivery import TextDelivery
from talkmore.cli import _load_session, _transcribe, _language_option, _print_runtime_stats


class RecordingPanel(A.NSPanel):
    """Display-only HUD: never accept input focus or appear as an AX target."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False

    def accessibilityIsIgnored(self):
        return True


class Application(A.NSObject):
    @objc.python_method
    def setup(self, model, language, duration):
        self.closed = False
        self.tap = None
        self.source = None
        self.settings_window = None
        self.capturing = False
        self.pending_hotkey = None
        self.captured_keys = set()
        self.permission_requesting = False
        self.config_error = ""
        try:
            self.hotkey = load_config()
        except Exception as exc:
            self.hotkey = HotkeyConfig.right_option()
            self.config_error = f"配置无效，暂用右 Option：{exc}"
        self.machine = HotkeyStateMachine(self.hotkey)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="talkmore-asr")
        self.delivery = TextDelivery(macos.copy_text, macos.modifiers_down,
                                     macos.paste, AppHelper.callLater)
        self.status = A.NSStatusBar.systemStatusBar().statusItemWithLength_(A.NSVariableStatusItemLength)
        self.menu = A.NSMenu.alloc().init()
        self.info = self.add_item("正在准备本地模型…", None)
        self.info.setEnabled_(False)
        self.add_item("快捷键设置…", "settings:")
        self.add_item("授权 / 重新检查权限…", "permissions:")
        self.add_item("复制最近结果", "copy:")
        self.add_item("取消录音", "cancel:")
        self.add_item("重试模型加载", "retry:")
        self.add_item("退出", "quit:")
        self.status.setMenu_(self.menu)
        self.status.button().setTitle_("TM …")
        self.create_panel()
        self.started_at = None
        self.state = "preparing"
        self.message = "正在准备本地模型…"

        def transcribe(loaded, audio):
            session, mx, load_seconds = loaded
            text, seconds = _transcribe(session, audio, _language_option(language))
            _print_runtime_stats(mx, load_seconds, seconds)
            return text

        self.controller = DictationController(
            recorder_factory=Recorder, submit=self.executor.submit, dispatch=AppHelper.callAfter,
            load_model=lambda: _load_session(model), transcribe=transcribe,
            deliver=self.delivery.deliver,
            on_update=self.update, max_duration=duration)
        self.timer = A.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "tick:", None, True)
        A.NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, A.NSRunLoopCommonModes)
        workspace = A.NSWorkspace.sharedWorkspace().notificationCenter()
        workspace.addObserver_selector_name_object_(self, "sleep:", A.NSWorkspaceWillSleepNotification, None)
        workspace.addObserver_selector_name_object_(self, "wake:", A.NSWorkspaceDidWakeNotification, None)
        self.install_tap()
        if not all(macos.permissions().values()):
            AppHelper.callLater(0.5, self.permissions_, None)

    @objc.python_method
    def add_item(self, title, action):
        item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action:
            item.setTarget_(self)
        self.menu.addItem_(item)
        return item

    @objc.python_method
    def create_panel(self):
        style = A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel
        self.panel = RecordingPanel.alloc().initWithContentRect_styleMask_backing_defer_(((0, 0), (280, 42)), style, A.NSBackingStoreBuffered, False)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setAccessibilityElement_(False)
        self.panel.setLevel_(A.NSFloatingWindowLevel)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces | A.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel_label = A.NSTextField.labelWithString_("录音中")
        self.panel_label.setAccessibilityElement_(False)
        self.panel_label.setFrame_(((14, 10), (252, 22)))
        self.panel.contentView().addSubview_(self.panel_label)
        screen = A.NSScreen.mainScreen().visibleFrame()
        self.panel.setFrameOrigin_((screen.origin.x + (screen.size.width - 280) / 2, screen.origin.y + 60))

    @objc.python_method
    def update(self, state, message):
        if self.closed:
            return
        previous = self.state
        self.state, self.message = state, message
        title = {"preparing": "TM …", "ready": "TM", "recording": "● TM", "transcribing": "TM …", "error": "TM !"}.get(state, "TM")
        self.status.button().setTitle_(title)
        self.info.setTitle_(message)
        self.status.button().setToolTip_(self.config_error or message)
        if state == "recording":
            self.started_at = time.monotonic()
            self.panel.orderFrontRegardless()
            A.NSSound.soundNamed_("Tink").play()
        elif previous == "recording":
            self.panel.orderOut_(None)
            self.started_at = None
            A.NSSound.soundNamed_("Tink").play()
        if state == "error" or (state == "ready" and any(word in message for word in ("手动粘贴", "失败", "错误", "没有识别"))):
            self.status.button().setTitle_("TM !")

    @objc.python_method
    def install_tap(self):
        if self.tap is not None:
            Q.CGEventTapEnable(self.tap, True)
            return True
        if not all(macos.permissions().values()):
            return False
        mask = sum(1 << event for event in (Q.kCGEventKeyDown, Q.kCGEventKeyUp, Q.kCGEventFlagsChanged))
        self.tap_callback = self.handle_event
        self.tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                    Q.kCGEventTapOptionDefault, mask, self.tap_callback, None)
        if self.tap is None:
            self.info.setTitle_("快捷键监听不可用，请授权并重新检查")
            return False
        self.source = Q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Q.CFRunLoopAddSource(Q.CFRunLoopGetMain(), self.source, Q.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(self.tap, True)
        return True

    @objc.python_method
    def handle_event(self, proxy, event_type, event, refcon):
        try:
            if event_type in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
                self.machine = HotkeyStateMachine(self.hotkey)
                self.controller.cancel()
                self.delivery.cancel()
                self.info.setTitle_("监听曾中断，录音已取消；正在恢复")
                Q.CGEventTapEnable(self.tap, True)
                return event
            if Q.CGEventGetIntegerValueField(event, Q.kCGEventSourceUserData) == 0x54414C4B:
                return event
            key = int(Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode))
            flags = int(Q.CGEventGetFlags(event))
            kind = {Q.kCGEventKeyDown: KEY_DOWN, Q.kCGEventKeyUp: KEY_UP, Q.kCGEventFlagsChanged: FLAGS_CHANGED}.get(event_type)
            if key in self.captured_keys and kind in (KEY_DOWN, KEY_UP):
                if kind == KEY_UP:
                    self.captured_keys.discard(key)
                return None
            if self.settings_window is not None and self.settings_window.isVisible():
                if self.capturing and kind == KEY_DOWN:
                    self.captured_keys.add(key)
                    if key == 53:
                        self.capturing = False
                        self.settings_label.setStringValue_("已取消录入")
                    else:
                        modifiers = [name for name, mask in (("command", Q.kCGEventFlagMaskCommand), ("control", Q.kCGEventFlagMaskControl), ("option", Q.kCGEventFlagMaskAlternate), ("shift", Q.kCGEventFlagMaskShift)) if flags & mask]
                        try:
                            self.pending_hotkey = HotkeyConfig.chord(key, modifiers)
                            self.capturing = False
                            native_event = A.NSEvent.eventWithCGEvent_(event)
                            character = native_event.charactersIgnoringModifiers() or f"键码 {key}"
                            self.settings_label.setStringValue_(f"待保存：{'+'.join(modifiers)} + {character.upper()}")
                        except ValueError:
                            self.settings_label.setStringValue_("请按修饰键 + 普通键")
                    return None
                return event
            if kind == KEY_DOWN and key == 53 and self.state == "recording":
                self.controller.cancel()
                self.machine = HotkeyStateMachine(self.hotkey)
                return None
            result = self.machine.handle_event(kind, key, flags, bool(Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat)))
            if result.action:
                AppHelper.callAfter(self.toggle)
            return None if result.consume else event
        except Exception as exc:
            AppHelper.callAfter(self.listener_error, str(exc))
            return event

    @objc.python_method
    def listener_error(self, message):
        self.controller.cancel()
        self.delivery.cancel()
        self.info.setTitle_(f"快捷键监听异常：{message}")

    @objc.python_method
    def toggle(self):
        if self.closed:
            return
        if all(macos.permissions().values()):
            self.controller.toggle()
        else:
            self.controller.cancel()
            self.delivery.cancel()
            self.info.setTitle_("权限未就绪，请使用菜单授权 / 重新检查权限")

    def tick_(self, timer):
        if self.closed:
            return
        self.controller.tick()
        if self.started_at is not None:
            elapsed = int(time.monotonic() - self.started_at)
            clock = f"{elapsed // 60:02}:{elapsed % 60:02}"
            self.status.button().setTitle_(f"● {clock}")
            self.panel_label.setStringValue_(f"● 录音中 {clock} · Esc 取消")
        # Permission checks run once per second, not inside keyboard callbacks.
        second = int(time.monotonic())
        if getattr(self, "last_permission_check", None) != second:
            self.last_permission_check = second
            if not all(macos.permissions().values()):
                self.controller.cancel()
                self.delivery.cancel()
                if self.tap:
                    Q.CGEventTapEnable(self.tap, False)
                self.info.setTitle_("权限未就绪，请授权 / 重新检查权限")

    def permissions_(self, sender):
        if self.permission_requesting:
            return
        self.permission_requesting = True
        def checked():
            self.permission_requesting = False
            values = macos.permissions()
            self.install_tap()
            missing = "、".join(name for name, granted in values.items() if not granted)
            self.info.setTitle_(f"请授权：{missing}；必要时重启 Talkmore" if missing else "权限已就绪")
            if missing:
                alert = A.NSAlert.alloc().init()
                alert.setMessageText_("Talkmore 需要系统权限")
                alert.setInformativeText_(f"尚缺：{missing}。请在系统设置 → 隐私与安全性中允许启动 Talkmore 的终端或 Python 进程。授权后使用菜单重新检查；必要时退出并重启。")
                alert.addButtonWithTitle_("打开系统设置")
                alert.addButtonWithTitle_("稍后")
                if alert.runModal() == A.NSAlertFirstButtonReturn:
                    A.NSWorkspace.sharedWorkspace().openURL_(A.NSURL.URLWithString_("x-apple.systempreferences:com.apple.preference.security"))
        macos.request_permissions(lambda: AppHelper.callAfter(checked))

    def settings_(self, sender):
        if self.state in ("recording", "transcribing"):
            self.info.setTitle_("请先结束录音和转写，再修改快捷键")
            return
        self.machine = HotkeyStateMachine(self.hotkey)
        self.capturing = False
        self.pending_hotkey = self.hotkey
        if self.settings_window is None:
            self.settings_window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((0, 0), (440, 210)), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable, A.NSBackingStoreBuffered, False)
            self.settings_window.setReleasedWhenClosed_(False)
            self.settings_window.setTitle_("Talkmore 快捷键设置")
            self.settings_label = A.NSTextField.labelWithString_("默认：右 Option")
            self.settings_label.setFrame_(((20, 150), (400, 40)))
            self.settings_window.contentView().addSubview_(self.settings_label)
            for title, action, x in (("右 Option / 恢复默认", "defaultKey:", 20), ("录入组合键", "captureKey:", 240)):
                button = A.NSButton.buttonWithTitle_target_action_(title, self, action)
                button.setFrame_(((x, 95), (190, 32)))
                self.settings_window.contentView().addSubview_(button)
            save = A.NSButton.buttonWithTitle_target_action_("保存", self, "saveKey:")
            save.setFrame_(((320, 30), (90, 32)))
            self.settings_window.contentView().addSubview_(save)
            self.settings_window.center()
        self.settings_label.setStringValue_(f"当前：{self.describe_key(self.hotkey)}")
        A.NSApp.activateIgnoringOtherApps_(True)
        self.settings_window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def describe_key(self, config):
        if config == HotkeyConfig.right_option():
            return "右 Option（松开切换）"
        symbols = {"command": "⌘", "control": "⌃", "option": "⌥", "shift": "⇧", "fn": "Fn"}
        names = {0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T", 31: "O", 32: "U", 34: "I", 35: "P", 37: "L", 38: "J", 40: "K", 45: "N", 46: "M", 49: "Space", 36: "Return", 53: "Esc"}
        return " + ".join([symbols[name] for name in ("control", "option", "shift", "command", "fn") if name in config.modifiers] + [names.get(config.keycode, f"键码 {config.keycode}")])

    def defaultKey_(self, sender):
        self.capturing = False
        self.pending_hotkey = HotkeyConfig.right_option()
        self.settings_label.setStringValue_("待保存：右 Option")

    def captureKey_(self, sender):
        if not self.install_tap():
            self.settings_label.setStringValue_("请先授权输入监控和辅助功能")
            return
        self.capturing = True
        self.settings_label.setStringValue_("请按组合键；Esc 取消")

    def saveKey_(self, sender):
        try:
            machine = HotkeyStateMachine(self.pending_hotkey)
            save_config(self.pending_hotkey)
            self.hotkey = self.pending_hotkey
            self.machine = machine
            self.capturing = False
            self.settings_window.orderOut_(None)
            self.config_error = ""
            self.info.setTitle_("快捷键已保存")
        except Exception as exc:
            self.settings_label.setStringValue_(f"保存失败，保留原配置：{exc}")

    def copy_(self, sender):
        if self.controller.latest_text:
            try:
                macos.copy_text(self.controller.latest_text)
                self.info.setTitle_("最近结果已复制")
            except Exception as exc:
                self.info.setTitle_(f"复制失败：{exc}")
        else:
            self.info.setTitle_("暂无转写结果")

    def cancel_(self, sender):
        self.delivery.cancel()
        self.controller.cancel()

    def retry_(self, sender):
        self.controller.retry()

    def sleep_(self, notification):
        self.delivery.cancel()
        self.controller.cancel()
        self.machine = HotkeyStateMachine(self.hotkey)
        if self.tap:
            Q.CGEventTapEnable(self.tap, False)

    def wake_(self, notification):
        self.install_tap()

    @objc.python_method
    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.delivery.cancel()
        self.controller.close()
        self.timer.invalidate()
        if self.tap:
            Q.CGEventTapEnable(self.tap, False)
        if self.source:
            Q.CFRunLoopRemoveSource(Q.CFRunLoopGetMain(), self.source, Q.kCFRunLoopCommonModes)
        A.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self)
        self.panel.orderOut_(None)
        self.executor.shutdown(wait=False, cancel_futures=True)
        A.NSStatusBar.systemStatusBar().removeStatusItem_(self.status)

    def quit_(self, sender):
        self.shutdown()
        A.NSApp.terminate_(None)

    def applicationWillTerminate_(self, notification):
        self.shutdown()


def launch(model, language, duration):
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    delegate = Application.alloc().init()
    app.setDelegate_(delegate)
    delegate.setup(model, language, duration)
    try:
        AppHelper.runEventLoop(installInterrupt=True)
    finally:
        delegate.shutdown()
    return 0
