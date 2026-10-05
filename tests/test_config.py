import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talkmore.config import default_config_path, load_config, save_config
from talkmore.hotkeys import HotkeyConfig


class ConfigTests(unittest.TestCase):
    def test_missing_file_uses_right_option_default(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_config(Path(directory) / "config.json"), HotkeyConfig.right_option())

    def test_right_option_and_chord_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(HotkeyConfig.chord(12, {"shift", "command"}), path)
            self.assertEqual(load_config(path), HotkeyConfig.chord(12, {"command", "shift"}))
            save_config(HotkeyConfig.right_option(), path)
            self.assertEqual(load_config(path), HotkeyConfig.right_option())

    def test_save_preserves_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"theme": "system", "hotkey": {"type": "right_option"}}))
            save_config(HotkeyConfig.chord(12, {"command"}), path)
            document = json.loads(path.read_text())
            self.assertEqual(document["theme"], "system")
            self.assertEqual(document["hotkey"]["type"], "chord")

    def test_failed_atomic_replace_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = '{"hotkey":{"type":"right_option"}}\n'
            path.write_text(original)
            with patch("talkmore.config.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(RuntimeError, "无法保存配置文件"):
                    save_config(HotkeyConfig.chord(12, {"command"}), path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_invalid_json_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("not json")
            with self.assertRaisesRegex(ValueError, "不是有效 JSON"):
                load_config(path)
            with self.assertRaisesRegex(ValueError, "不是有效 JSON"):
                save_config(HotkeyConfig.right_option(), path)
            self.assertEqual(path.read_text(), "not json")

    def test_default_path_is_application_support_location(self):
        with patch.object(Path, "home", return_value=Path("/tmp/talkmore-home")):
            self.assertEqual(
                default_config_path(),
                Path("/tmp/talkmore-home/Library/Application Support/Talkmore/config.json"),
            )

    def test_invalid_persisted_hotkey_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"hotkey": {"type": "chord", "keycode": 61, "modifiers": ["command"]}}))
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
