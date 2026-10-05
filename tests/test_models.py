import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from talkmore.models import DEFAULT_MODEL, MODEL_ALIASES, DOWNLOAD_LIMIT, checked_files, ensure_model, resolve_model
from talkmore.cli import _build_parser


class ModelSelectionTests(unittest.TestCase):
    def test_default_is_8bit_for_both_commands(self):
        self.assertEqual(DEFAULT_MODEL, MODEL_ALIASES["8bit"])
        for argv in (["record"], ["transcribe", "audio.wav"]):
            self.assertEqual(_build_parser().parse_args(argv).model, DEFAULT_MODEL)

    def test_aliases_resolve_for_both_commands(self):
        for alias, repo in MODEL_ALIASES.items():
            for argv in (["record"], ["transcribe", "audio.wav"]):
                args = _build_parser().parse_args([*argv, "--model", alias])
                self.assertEqual(resolve_model(args.model), repo)

    def test_full_id_is_preserved(self):
        for model in (*MODEL_ALIASES.values(), "example/custom-model"):
            self.assertEqual(resolve_model(model), model)

    def test_existing_local_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(resolve_model(directory), directory)

    def test_remote_alias_is_resolved_before_metadata_lookup(self):
        with patch.dict(MODEL_ALIASES, {"test-alias": "example/custom-model"}), patch("huggingface_hub.HfApi") as api:
            api.return_value.model_info.side_effect = RuntimeError("metadata sentinel")
            with self.assertRaisesRegex(RuntimeError, "metadata sentinel"):
                ensure_model("test-alias")
            api.return_value.model_info.assert_called_once_with("example/custom-model", files_metadata=True)


class ModelDownloadGuardTests(unittest.TestCase):
    def file(self, name, size):
        return SimpleNamespace(rfilename=name, size=size)

    def test_weights_over_limit_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "超过 1 GB"):
            checked_files([self.file("model.safetensors", DOWNLOAD_LIMIT + 1)])

    def test_combined_download_over_limit_is_rejected(self):
        with self.assertRaises(RuntimeError):
            checked_files([self.file("a.safetensors", 600_000_000), self.file("b.safetensors", 600_000_000)])

    def test_unknown_size_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            checked_files([self.file("model.safetensors", None)])

    def test_only_runtime_files_count(self):
        files, total = checked_files([self.file("model.safetensors", 517_000_000), self.file("README.md", None)])
        self.assertEqual(total, 517_000_000)
        self.assertEqual(len(files), 1)

    def test_local_directory_does_not_query_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}")
            (root / "model.safetensors").touch()
            with patch("huggingface_hub.HfApi", side_effect=AssertionError("network")):
                self.assertEqual(ensure_model(directory), str(root.resolve()))


if __name__ == "__main__":
    unittest.main()
