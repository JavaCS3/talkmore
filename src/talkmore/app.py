"""Command-launched macOS menu-bar dictation application."""
from __future__ import annotations
import fcntl
import os
import platform
import sys
from pathlib import Path


def run_app(model, language="auto", max_duration=300.0):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("菜单栏应用仅支持 Apple Silicon macOS")
    # A process lock has no stale-lock recovery race and is released on crashes.
    lock_path = Path("/tmp") / f"talkmore-{os.getuid()}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(descriptor, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Talkmore 菜单栏应用已在运行")
    try:
        from talkmore.native_app import launch
        return launch(model, language, max_duration)
    finally:
        lock.close()
