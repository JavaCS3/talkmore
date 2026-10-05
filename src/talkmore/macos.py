"""macOS permission, clipboard and system keyboard-injection adapters."""
import AppKit as A
import Quartz as Q
import ApplicationServices as AX
import AVFoundation as AV

MODIFIERS = (Q.kCGEventFlagMaskCommand | Q.kCGEventFlagMaskControl |
             Q.kCGEventFlagMaskAlternate | Q.kCGEventFlagMaskShift | Q.kCGEventFlagMaskSecondaryFn)


def permissions():
    return {
        "麦克风": AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio) == AV.AVAuthorizationStatusAuthorized,
        "输入监控": bool(Q.CGPreflightListenEventAccess()),
        "辅助功能": bool(AX.AXIsProcessTrusted()),
    }


def request_permissions(callback):
    AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
    Q.CGRequestListenEventAccess()
    if AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio) == AV.AVAuthorizationStatusNotDetermined:
        AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, lambda granted: callback())
    else:
        callback()


def copy_text(text):
    board = A.NSPasteboard.generalPasteboard()
    board.clearContents()
    if not board.setString_forType_(text, A.NSPasteboardTypeString):
        raise RuntimeError("系统剪贴板拒绝写入")


def modifiers_down():
    return bool(Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState) & MODIFIERS)


def paste():
    """Post a single complete Command-V key sequence to the current focus.

    No application activation, AX element lookup, caret query or text mutation.
    """
    if not AX.AXIsProcessTrusted():
        raise RuntimeError("辅助功能权限不可用，无法发送系统粘贴按键")
    source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)
    if source is None:
        raise RuntimeError("无法创建系统按键事件源")
    events = []
    for keycode, down, flags in (
        (55, True, Q.kCGEventFlagMaskCommand),
        (9, True, Q.kCGEventFlagMaskCommand),
        (9, False, Q.kCGEventFlagMaskCommand),
        (55, False, 0),
    ):
        event = Q.CGEventCreateKeyboardEvent(source, keycode, down)
        if event is None:
            raise RuntimeError("无法创建粘贴按键事件")
        Q.CGEventSetFlags(event, flags)
        Q.CGEventSetIntegerValueField(event, Q.kCGEventSourceUserData, 0x54414C4B)
        events.append(event)
    try:
        for event in events:
            Q.CGEventPost(Q.kCGHIDEventTap, event)
    except Exception:
        # Release keys after a partially posted sequence, never retry V-down.
        for event in events[2:]:
            try:
                Q.CGEventPost(Q.kCGHIDEventTap, event)
            except Exception:
                pass
        raise
