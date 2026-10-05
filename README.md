# Talkmore

Apple Silicon Mac 上的本地语音转文字 CLI。使用 uv 管理 Python 环境，默认 Qwen3-ASR 0.6B 8-bit 量化模型。只做 ASR，不额外改写或去除口头语。

## 使用

```bash
uv sync
uv run talkmore record
```

按 Enter 开始录音，再按 Enter 停止，转写结果输出到终端。默认最长 120 秒，到时自动结束。首次使用需允许运行终端的应用访问麦克风（macOS 系统设置 → 隐私与安全性 → 麦克风）。

```bash
uv run talkmore record --copy
uv run talkmore record --save-audio recordings/demo.wav
uv run talkmore transcribe recordings/demo.wav
uv run talkmore transcribe recordings/demo.wav --language Chinese
uv run talkmore record --help
```

`--copy` 将结果复制到 macOS 剪贴板。默认不保存录音；`--save-audio` 明确保存 WAV。WAV 可以直接读取；其他格式可能需要系统安装 ffmpeg。

## 模型与离线运行

默认模型：`moona3k/mlx-qwen3-asr-0.6b-8bit`。首次转写会先查询 Hugging Face 文件大小，只下载必要的配置、tokenizer 和权重到项目 `.models/`。下载总量超过 **1 GB（1,000,000,000 bytes）** 或大小无法确认时直接停止，需要先取得用户审批。

`record` 和 `transcribe` 都支持模型切换：

```bash
uv run talkmore record --copy                # 默认 8-bit
uv run talkmore record --model 4bit --copy    # 较省内存的混合量化版
uv run talkmore transcribe recordings/demo.wav --model 8bit
uv run talkmore transcribe recordings/demo.wav --model moona3k/mlx-qwen3-asr-0.6b-4bit
```

`4bit` 对应解码器 4-bit、编码器 8-bit 的 0.6B 模型；`8bit` 对应编码器和解码器均为 8-bit 的 0.6B 模型。各模型分别缓存，切换不会删除旧模型，每次进程只加载选中的一个模型。终端会显示当前模型。显式参数只对当前命令生效，不会改变默认配置。

模型下载完成后，通过本地完整性清单复用，不再查询网络。也可显式指定已有模型目录：

```bash
uv run talkmore transcribe recordings/demo.wav --model /absolute/path/to/model
```

推理只在本机进行，不上传音频。自动语言识别为默认设置。退出进程后模型资源释放；每次启动都会重新加载模型。当前没有后台常驻、全局快捷键、自动粘贴、静音自动结束或文本整理。

## 开发与验证

```bash
uv run python -m unittest discover -s tests -v
```

模型文件大小不等于实际运行内存，实际峰值需包括 CPU 进程与 Metal 分配。转写性能和中文效果需在本机使用真实录音验证。

每次转写会在 stderr 显示模型加载时间、推理时间、进程峰值 RSS 和 MLX 峰值分配；后两者的内存统计存在重叠，不能相加。stdout 只输出转写文字，支持重定向到文件。

## 本机验证（2026-10-05）

- uv 已安装依赖并生成 `uv.lock`；初版 4-bit 模型运行文件实际下载总量 542.1 MB。
- 8 项测试通过，覆盖超过 1 GB/未知大小的下载拦截、使用本地模型无需网络，以及录音停止/启动失败时释放设备。
- 设置 `HF_HUB_OFFLINE=1`，用 macOS 系统语音合成的中文 WAV 完成真实 Metal 推理，输出与测试句子一致：`今天下午需要跟客户确认一下项目的交付时间。`
- 此单次短音频测试：加载 0.24 秒、推理 1.97 秒、进程峰值 RSS 677 MiB、MLX 峰值 625 MiB。不是预热后的性能基准，也不能代表中文口语或更长音频表现。
- 麦克风流程已通过模拟设备测试，尚未完成真实麦克风录音验证。请运行 `uv run talkmore record` 做首次试用。
- 沙箱/无头环境可能无法访问 Metal GPU，需在正常 macOS 终端运行。

### 模型切换验证（2026-10-05）

默认已改为 8-bit，支持 `--model 4bit` / `--model 8bit`，保留完整模型 ID 和本地目录。13 项测试通过。8-bit 必要文件下载总量为 840.1 MB，下载前经 1 GB 阈值检查；4-bit 缓存保留。

两次分别启动 CLI，设置 `HF_HUB_OFFLINE=1`，转写同一段 4.74 秒中文合成 WAV，均输出：`今天下午需要跟客户确认一下项目的交付时间。`

| 模型 | 加载时间 | 推理时间 | 进程峰值 RSS | MLX 峰值 |
|---|---:|---:|---:|---:|
| 4-bit（通过简写选择） | 0.30 秒 | 0.23 秒 | 666 MiB | 639 MiB |
| 8-bit（默认选择） | 0.17 秒 | 0.29 秒 | 950 MiB | 923 MiB |

这些是单次运行结果，可能受系统缓存影响；不是冷启动/预热性能基准。单句识别一致无法证明两者在自然口语上的准确率相同。进程 RSS 和 MLX 峰值存在重叠，不能相加。
