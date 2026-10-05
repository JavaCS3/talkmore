"""Pure-Python hotkey configuration and event state machine.

The macOS event-tap adapter should pass Quartz event types as ``KEY_DOWN``,
``KEY_UP`` or ``FLAGS_CHANGED``, along with the event keycode, the raw Quartz
modifier flags, and the keyboard autorepeat bit. The module deliberately does
not import Quartz or AppKit, so its behavior is testable on any platform.

The default hotkey is the physical right Option key (virtual keycode 61). A
tap/release toggles recording only when no other key or modifier overlapped
that press. A configured chord consists of one ordinary keycode and one or
more modifier names. Matching chord key-downs and their corresponding key-ups
are consumed; repeats are consumed without firing another action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Literal


KEY_DOWN = "key_down"
KEY_UP = "key_up"
FLAGS_CHANGED = "flags_changed"

TOGGLE_RECORDING = "toggle_recording"

# Quartz CGEventFlags values. Keep these numeric values here rather than
# importing Quartz, allowing the state machine and its tests to stay portable.
MODIFIER_FLAG_MASKS: dict[str, int] = {
    "shift": 0x00020000,
    "control": 0x00040000,
    "option": 0x00080000,
    "command": 0x00100000,
    "fn": 0x00800000,
}
_MODIFIER_FLAGS = 0
for _flag in MODIFIER_FLAG_MASKS.values():
    _MODIFIER_FLAGS |= _flag

RIGHT_OPTION_KEYCODE = 61
LEFT_OPTION_KEYCODE = 58

# ANSI and ISO modifier keycodes understood by macOS. Including both sides
# prevents a modifier from being configured as the ordinary chord key.
MODIFIER_KEYCODES = frozenset({54, 55, 56, 58, 59, 60, 61, 62, 63})
_KEYCODE_MODIFIER = {
    54: "command",
    55: "command",
    56: "shift",
    60: "shift",
    58: "option",
    61: "option",
    59: "control",
    62: "control",
    63: "fn",
}


@dataclass(frozen=True)
class HotkeyConfig:
    """A right-Option trigger or a regular key chord.

    Use :meth:`right_option` for the MVP default or :meth:`chord` for a
    configurable modifier-plus-key shortcut. ``keycode`` is a macOS virtual
    keycode; ``modifiers`` contains names from ``command/control/option/shift/fn``.
    """

    kind: Literal["right_option", "chord"] = "right_option"
    keycode: int | None = None
    modifiers: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.kind == "right_option":
            if self.keycode is not None or self.modifiers:
                raise ValueError("right_option hotkey cannot include a keycode or modifiers")
            return
        if self.kind != "chord":
            raise ValueError(f"unsupported hotkey type: {self.kind!r}")
        if isinstance(self.keycode, bool) or not isinstance(self.keycode, int):
            raise ValueError("chord keycode must be an integer")
        if not 0 <= self.keycode <= 127:
            raise ValueError("chord keycode must be between 0 and 127")
        if self.keycode in MODIFIER_KEYCODES:
            raise ValueError("chord keycode must be a normal, non-modifier key")
        if not self.modifiers:
            raise ValueError("a chord must contain at least one modifier")
        unknown = self.modifiers - MODIFIER_FLAG_MASKS.keys()
        if unknown:
            raise ValueError(f"unsupported modifier(s): {', '.join(sorted(unknown))}")

    @classmethod
    def right_option(cls) -> "HotkeyConfig":
        return cls()

    @classmethod
    def chord(cls, keycode: int, modifiers: Iterable[str]) -> "HotkeyConfig":
        return cls(kind="chord", keycode=keycode, modifiers=frozenset(modifiers))

    def to_dict(self) -> dict[str, object]:
        if self.kind == "right_option":
            return {"type": "right_option"}
        return {
            "type": "chord",
            "keycode": self.keycode,
            "modifiers": sorted(self.modifiers),
        }

    @classmethod
    def from_dict(cls, value: object) -> "HotkeyConfig":
        if not isinstance(value, dict):
            raise ValueError("hotkey config must be a JSON object")
        kind = value.get("type", "right_option")
        if kind == "right_option":
            if set(value) != {"type"}:
                raise ValueError("right_option hotkey only accepts the type field")
            return cls.right_option()
        if kind != "chord":
            raise ValueError(f"unsupported hotkey type: {kind!r}")
        if set(value) != {"type", "keycode", "modifiers"}:
            raise ValueError("chord hotkey requires type, keycode, and modifiers fields")
        modifiers = value["modifiers"]
        if not isinstance(modifiers, list) or any(not isinstance(item, str) for item in modifiers):
            raise ValueError("chord modifiers must be a list of modifier names")
        return cls.chord(value["keycode"], modifiers)


@dataclass(frozen=True)
class HotkeyResult:
    """What an event-tap adapter should do with one keyboard event."""

    consume: bool = False
    action: str | None = None


class HotkeyStateMachine:
    """Convert low-level keyboard events into one-shot hotkey actions."""

    def __init__(self, config: HotkeyConfig | None = None) -> None:
        self.config = config or HotkeyConfig.right_option()
        self._right_option_down = False
        self._right_option_chorded = False
        self._held_keycodes: set[int] = set()
        self._held_modifier_keycodes: set[int] = set()
        self._chord_key_down = False

    def reset(self) -> None:
        """Forget in-flight key state, for example when an event tap is rebuilt."""
        self._right_option_down = False
        self._right_option_chorded = False
        self._held_keycodes.clear()
        self._held_modifier_keycodes.clear()
        self._chord_key_down = False

    def handle_event(
        self,
        event_type: str,
        keycode: int,
        flags: int = 0,
        autorepeat: bool = False,
    ) -> HotkeyResult:
        """Process an event and report whether it is consumed or triggers action.

        The ``flags`` argument is the event's Quartz modifier-flags bitmask.
        ``flagsChanged`` events identify which physical modifier changed using
        ``keycode``; that identity is tracked separately because Quartz's Option
        flag alone does not distinguish left from right Option.
        """
        if event_type == FLAGS_CHANGED:
            return self._handle_flags_changed(keycode, flags, autorepeat)
        if event_type == KEY_DOWN:
            return self._handle_key_down(keycode, flags, autorepeat)
        if event_type == KEY_UP:
            return self._handle_key_up(keycode)
        return HotkeyResult()

    def _handle_flags_changed(self, keycode: int, flags: int, autorepeat: bool) -> HotkeyResult:
        if keycode not in MODIFIER_KEYCODES or autorepeat:
            if self._right_option_down and keycode != RIGHT_OPTION_KEYCODE:
                self._right_option_chorded = True
            return HotkeyResult()

        was_down = keycode in self._held_modifier_keycodes
        modifier = _KEYCODE_MODIFIER[keycode]
        modifier_flag = MODIFIER_FLAG_MASKS[modifier]
        # Quartz modifier flags are aggregate for left/right pairs. They still
        # provide a useful guard against a release event arriving after this
        # state machine was reset or attached mid-press: a cleared modifier
        # flag cannot represent a new physical key-down.
        if not was_down and not (flags & modifier_flag):
            return HotkeyResult()
        if was_down:
            self._held_modifier_keycodes.remove(keycode)
        else:
            self._held_modifier_keycodes.add(keycode)

        if self.config.kind == "right_option":
            if keycode == RIGHT_OPTION_KEYCODE:
                if not was_down:
                    # On press, catch modifiers that were already held, as well
                    # as regular keys that began before right Option.
                    other_modifier = any(
                        code != RIGHT_OPTION_KEYCODE
                        for code in self._held_modifier_keycodes
                    )
                    other_flags = flags & (_MODIFIER_FLAGS & ~MODIFIER_FLAG_MASKS["option"])
                    if other_modifier or other_flags or self._held_keycodes:
                        self._right_option_chorded = True
                    self._right_option_down = True
                else:
                    should_toggle = self._right_option_down and not self._right_option_chorded
                    # If the other side of Option is still held, this was not an
                    # isolated right-Option tap even though the physical right
                    # key has now been released.
                    if any(code != RIGHT_OPTION_KEYCODE for code in self._held_modifier_keycodes):
                        should_toggle = False
                    self._right_option_down = False
                    self._right_option_chorded = False
                    if should_toggle:
                        return HotkeyResult(action=TOGGLE_RECORDING)
            elif self._right_option_down:
                self._right_option_chorded = True
            return HotkeyResult()

        # Modifier key events remain available to the operating system when a
        # regular chord is configured; only the chord's ordinary key is eaten.
        return HotkeyResult()

    def _handle_key_down(self, keycode: int, flags: int, autorepeat: bool) -> HotkeyResult:
        if self._right_option_down:
            self._right_option_chorded = True
        already_down = keycode in self._held_keycodes
        self._held_keycodes.add(keycode)

        if self.config.kind != "chord" or keycode != self.config.keycode:
            return HotkeyResult()

        # Once the first matching key-down has captured this key, consume all
        # repeats through its key-up even if the user releases a modifier first.
        if self._chord_key_down:
            return HotkeyResult(consume=True)

        expected_flags = 0
        for modifier in self.config.modifiers:
            expected_flags |= MODIFIER_FLAG_MASKS[modifier]
        if flags & _MODIFIER_FLAGS != expected_flags:
            return HotkeyResult()

        if already_down or autorepeat:
            self._chord_key_down = True
            return HotkeyResult(consume=True)
        self._chord_key_down = True
        return HotkeyResult(consume=True, action=TOGGLE_RECORDING)

    def _handle_key_up(self, keycode: int) -> HotkeyResult:
        if self._right_option_down:
            self._right_option_chorded = True
        self._held_keycodes.discard(keycode)
        if self.config.kind == "chord" and keycode == self.config.keycode and self._chord_key_down:
            self._chord_key_down = False
            return HotkeyResult(consume=True)
        return HotkeyResult()
