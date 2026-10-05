"""Nonblocking clipboard delivery to the system's current keyboard focus."""
from __future__ import annotations
import time


class TextDelivery:
    def __init__(self, copy, modifiers_down, paste, later, clock=time.monotonic):
        self.copy = copy
        self.modifiers_down = modifiers_down
        self.paste = paste
        self.later = later
        self.clock = clock
        self.generation = 0

    def cancel(self):
        self.generation += 1

    def deliver(self, text, done):
        self.cancel()
        generation = self.generation
        try:
            self.copy(text)
        except Exception as exc:
            done(f"无法复制文字：{exc}")
            return
        deadline = self.clock() + 2.0
        finished = False

        def attempt():
            nonlocal finished
            if finished or generation != self.generation:
                return
            try:
                if self.modifiers_down():
                    if self.clock() >= deadline:
                        finished = True
                        done("文字已复制，请松开修饰键后手动粘贴")
                    else:
                        self.later(0.05, attempt)
                else:
                    # Never retry an injected paste: the destination may have received it.
                    finished = True
                    self.paste()
                    done("已发送粘贴；文字保留在剪贴板")
            except Exception as exc:
                finished = True
                done(f"文字已复制，自动粘贴不可用：{exc}")
        attempt()
