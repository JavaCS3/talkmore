"""Persistence for the small Talkmore application configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from talkmore.hotkeys import HotkeyConfig


def default_config_path() -> Path:
    """Return ``~/Library/Application Support/Talkmore/config.json``."""
    return Path.home() / "Library" / "Application Support" / "Talkmore" / "config.json"


def load_config(path: str | os.PathLike[str] | None = None) -> HotkeyConfig:
    """Load the configured hotkey, defaulting to right Option when absent.

    Other top-level settings are ignored so they can coexist in the same JSON
    document as this initial hotkey-only configuration API.
    """
    config_path = Path(path) if path is not None else default_config_path()
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HotkeyConfig.right_option()
    except OSError as exc:
        raise RuntimeError(f"无法读取配置文件 {config_path}: {exc}") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON：{config_path}") from exc
    if not isinstance(document, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")
    if "hotkey" not in document:
        return HotkeyConfig.right_option()
    return HotkeyConfig.from_dict(document["hotkey"])


def save_config(
    hotkey: HotkeyConfig,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically save a hotkey config and return its destination path.

    The hotkey is validated before touching the file. The new JSON is written
    and flushed to a sibling temporary file, then atomically replaced into
    place. If writing or replacement fails before the rename, the prior config
    remains intact and the temporary file is removed.
    """
    if not isinstance(hotkey, HotkeyConfig):
        raise TypeError("hotkey must be a HotkeyConfig")

    config_path = Path(path) if path is not None else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve unrelated top-level settings if another part of the app has
    # already added them. Invalid existing JSON is not silently overwritten.
    try:
        existing_raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        document: dict[str, object] = {}
    except OSError as exc:
        raise RuntimeError(f"无法读取配置文件 {config_path}: {exc}") from exc
    else:
        try:
            document = json.loads(existing_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"配置文件不是有效 JSON：{config_path}") from exc
        if not isinstance(document, dict):
            raise ValueError("配置文件根节点必须是 JSON 对象")

    document["hotkey"] = hotkey.to_dict()
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, config_path)
        temp_name = None
    except OSError as exc:
        raise RuntimeError(f"无法保存配置文件 {config_path}: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    return config_path
