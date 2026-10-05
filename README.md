# Talkmore

Apple Silicon Mac 上的本地语音输入工具。把光标放在输入框里，按一下快捷键说话，再按一下，文字就会粘贴到当前光标处。

使用 Qwen3-ASR 与 MLX 在本机识别，支持菜单栏听写和命令行转写。只做语音转文字，不额外改写、润色或去除口头语。

> 当前为早期 MVP：需要从终端启动，尚未提供可双击安装的 `.app`。真实全局快捷键及跨应用粘贴仍需实机验证，详见[验证记录](docs/validation.md)。

## 用户体验

1. 启动 Talkmore，菜单栏出现 `TM`，等待模型准备和系统权限就绪。
2. 在聊天、笔记或编辑器的输入框中放好光标。
3. **轻按并松开右 Option** 开始录音，浮层显示录音时间，不抢输入焦点。
4. 再轻按并松开右 Option，结束录音并开始转写。
5. 转写文字写入剪贴板，并向当前接收键盘输入的应用发送一次 `⌘V`，不自动发送 Enter。

按 **Esc** 取消，默认 **5 分钟**自动停止并转写。左 Option 不触发；右 Option 配合其他按键输入不触发。进入和离开录音状态时播放短提示音。准备模型和转写期间不会开始另一段录音。

粘贴跟随转写完成时的键盘焦点：如果途中切换应用或移动光标，文字会粘贴到新的位置。菜单中的“快捷键设置”可改为“修饰键 + 普通键”，保存后立即生效，请避开系统或常用应用快捷键。

## 安装与启动

需要 **Apple Silicon（M 系列）Mac、Python 3.11+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)**。依赖安装和首次模型下载需要网络。

下载或克隆本仓库后，在仓库目录运行：

```bash
uv sync --frozen
uv run talkmore app
```

保持启动终端运行，通过菜单栏“退出”结束应用。重复启动会被单实例锁拦截。

首次启动会下载默认 8-bit 模型，2026-10-05 本机下载记录约为 **840 MB**，实际大小以下载前提示为准。模型加载完成后会在进程内复用。默认模型无需 Talkmore 账号或 API Key。

## 首次授权

菜单栏模式需要三项 macOS 权限：

| 权限 | 用途 |
| --- | --- |
| 麦克风 | 录制你主动开始的一段语音 |
| 输入监控 | 监听全局快捷键与取消按键 |
| 辅助功能 | 使用全局键盘事件监听和发送系统粘贴按键 |

1. 启动后，按系统提示授权；也可点击菜单栏 `TM` → **“授权 / 重新检查权限…”**。
2. 打开 **系统设置 → 隐私与安全性**，分别检查“麦克风”“输入监控”“辅助功能”。
3. 允许实际启动 Talkmore 的终端应用或 Python 进程。当前从源码启动，系统显示的名称可能是 Terminal、iTerm 或 Python，取决于启动环境。
4. 回到菜单栏重新检查；若仍未生效，退出 Talkmore，必要时重启启动它的终端，再运行 `uv run talkmore app`。

`TM !` 或菜单首行会提示缺失权限及运行错误。只有命令行录音时需要麦克风权限；转写已有文件不需要上述三项权限。详细排查见[权限与隐私说明](docs/permissions.md)。

## 常用操作

### 菜单栏听写

```bash
uv run talkmore app --model 4bit
uv run talkmore app --language Chinese --max-duration 300
```

菜单还提供“取消录音”“复制最近结果”“重试模型加载”。快捷键配置保存在 `~/Library/Application Support/Talkmore/config.json`；模型、语言和录音上限通过启动参数选择，只对当前启动生效。目前不支持 Fn 单键和开机自启。

### 命令行录音与文件转写

```bash
uv run talkmore record
uv run talkmore record --copy
uv run talkmore record --save-audio recordings/demo.wav
uv run talkmore transcribe recordings/demo.wav
uv run talkmore transcribe recordings/demo.wav --language Chinese
uv run talkmore record --help
```

`record` 按 Enter 开始，再按 Enter 停止；默认最长 300 秒。`--copy` 复制文字到剪贴板，`--save-audio` 显式保存 WAV。WAV 可直接读取，其他格式可能需要系统安装 ffmpeg。stdout 输出文字，stderr 输出运行信息，可将文字重定向到文件。

### 模型与离线运行

| 参数 | 模型 | 量化方式 |
| --- | --- | --- |
| `8bit`（默认） | `moona3k/mlx-qwen3-asr-0.6b-8bit` | 编码器与解码器均为 8-bit |
| `4bit` | `moona3k/mlx-qwen3-asr-0.6b-4bit` | 编码器 8-bit，解码器 4-bit |

三个命令均支持 `--model`，也可传入完整 Hugging Face 模型 ID 或已有本地目录：

```bash
uv run talkmore record --model 4bit --copy
uv run talkmore transcribe recordings/demo.wav --model /absolute/path/to/model
```

首次下载前检查文件大小，只下载运行所需配置、tokenizer 和权重到仓库 `.models/`。总量超过 **1 GB（1,000,000,000 bytes）** 或大小无法确认时停止下载；如需更大模型，请自行确认并准备模型，再传入本地目录。

缓存完整后通过本地文件清单复用，不再查询网络，可离线识别。两种模型分别缓存，切换不会删除旧模型，每个进程只加载选中的一个模型。自动语言识别为默认设置；CLI 每次启动重新加载模型。

## 隐私与使用边界

- 识别在本机进行，不上传音频；依赖安装和首次模型下载会访问网络。
- 默认不保存录音或转写历史。菜单栏模式的音频和最近结果只保存在进程内存；CLI 可通过 `--save-audio` 保存录音。
- 转写结果会留在系统剪贴板，其他剪贴板工具可能读取或保存它。粘贴会覆盖原剪贴板内容。
- Talkmore 不读取目标应用、输入框或光标位置，不遍历辅助功能树，也不自动切换应用。
- “已发送粘贴”只表示按键已发送，不代表目标应用确认接收。若粘贴失败，可用“复制最近结果”手动粘贴。
- 取消或休眠会丢弃当前任务的待交付结果；正在运行的模型推理无法立即中断，后续任务仍串行执行。

目前没有静音自动结束或文本整理。模型文件大小不等于运行内存；中文口语效果与实际性能需在自己的设备上验证。

## 开发与反馈

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
```

参见[开发说明](docs/development.md)、[贡献指南](CONTRIBUTING.md)和[本机验证记录](docs/validation.md)。报告问题时请附上 macOS 版本、芯片、启动命令、权限状态和错误信息，并去除私人音频与文字。

## 开源许可与致谢

Talkmore 自身代码采用 [MIT License](LICENSE)。允许使用、修改、分发及商用，需保留版权和许可证声明；软件按现状提供，不作担保。

模型权重与第三方依赖遵循各自许可证，不属于 Talkmore 的 MIT 授权范围。默认 [8-bit](https://huggingface.co/moona3k/mlx-qwen3-asr-0.6b-8bit) 与 [4-bit](https://huggingface.co/moona3k/mlx-qwen3-asr-0.6b-4bit) 模型卡标注 Apache-2.0；本仓库不分发模型权重。

感谢 [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)、[mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr)、[MLX](https://github.com/ml-explore/mlx)、[PyObjC](https://github.com/ronaldoussoren/pyobjc) 和 [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/index)。
