"""Resolve models locally, checking remote size before any weight download."""

from fnmatch import fnmatch
from pathlib import Path
import json
import sys

MODEL_ALIASES = {
    "4bit": "moona3k/mlx-qwen3-asr-0.6b-4bit",
    "8bit": "moona3k/mlx-qwen3-asr-0.6b-8bit",
}
DEFAULT_MODEL = MODEL_ALIASES["8bit"]
DOWNLOAD_LIMIT = 1_000_000_000  # Automatic download limit: decimal 1 GB.
PATTERNS = ("*.json", "*.safetensors", "*.txt", "*.model")


def resolve_model(model: str) -> str:
    """Preserve local directories and full IDs; expand built-in short names."""
    if Path(model).expanduser().is_dir():
        return model
    return MODEL_ALIASES.get(model, model)


def checked_files(siblings):
    files = [f for f in siblings if any(fnmatch(f.rfilename, p) for p in PATTERNS)]
    if not files or any(f.size is None for f in files):
        raise RuntimeError("无法确认模型下载大小，已停止下载。请先确认模型文件及大小。")
    total = sum(f.size for f in files)
    if total > DOWNLOAD_LIMIT:
        raise RuntimeError(
            f"模型文件共 {total / 1e9:.2f} GB，超过 1 GB 自动下载上限，未下载。"
            "请确认模型文件及大小，自行下载后通过 --model 指定本地目录。"
        )
    return files, total


def ensure_model(model: str = DEFAULT_MODEL) -> str:
    model = resolve_model(model)
    local = Path(model).expanduser()
    if local.is_dir():
        if not (local / "config.json").is_file() or not list(local.glob("*.safetensors")):
            raise RuntimeError(f"模型目录缺少 config.json 或 safetensors 权重：{local}")
        return str(local.resolve())
    if local.exists() or model.startswith(("/", ".", "~")):
        raise RuntimeError(f"本地模型目录不存在或无效：{local}")

    # Keep weights in the project and pass a local path to the upstream runtime.
    root = Path(__file__).resolve().parents[2] / ".models" / model.replace("/", "--")
    manifest = root / ".talkmore-manifest.json"
    if manifest.is_file():
        metadata = json.loads(manifest.read_text())
        files = metadata.get("files", [])
        if files and all((root / f["name"]).is_file() and (root / f["name"]).stat().st_size == f["size"] for f in files):
            return str(root)

    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(model, files_metadata=True)
    files, total = checked_files(info.siblings)
    print(f"模型下载大小：{total / 1e6:.1f} MB（低于 1 GB）；下载到 {root}", file=sys.stderr)
    snapshot_download(
        repo_id=model, revision=info.sha, local_dir=str(root),
        allow_patterns=[f.rfilename for f in files],
    )
    manifest.write_text(json.dumps({"revision": info.sha, "files": [{"name": f.rfilename, "size": f.size} for f in files]}))
    return str(root)
