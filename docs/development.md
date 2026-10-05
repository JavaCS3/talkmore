# 开发说明

## 环境与测试

在 Apple Silicon macOS 上，安装 Python 3.11+ 与 uv：

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
uv run talkmore --help
```

测试涵盖模型选择及下载限制、录音生命周期、取消与超时、快捷键状态机、配置、剪贴板交付和 macOS 桥接。部分测试需要 macOS 原生框架；mock 测试不等于真实系统授权或跨应用粘贴验证。正常图形会话中的 Metal 推理、麦克风和全局按键应在实机检查。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `src/talkmore/cli.py` | CLI 参数、录音／文件转写入口、模型 Session 与运行统计 |
| `src/talkmore/models.py` | 模型别名、本地缓存、下载大小检查 |
| `src/talkmore/app.py` | 平台检查、菜单栏入口及单实例锁 |
| `src/talkmore/native_app.py` | AppKit 菜单栏、浮层、设置窗口与全局事件适配 |
| `src/talkmore/controller.py` | 异步模型准备、录音、转写、取消与交付调度 |
| `src/talkmore/recording.py` | 麦克风录音、设备释放与音频检查 |
| `src/talkmore/hotkeys.py` | 不依赖 AppKit 的快捷键状态机 |
| `src/talkmore/config.py` | 快捷键配置加载与保存 |
| `src/talkmore/delivery.py` | 剪贴板交付、修饰键等待及单次粘贴 |
| `src/talkmore/macos.py` | 系统权限、剪贴板和键盘事件接口 |

模型下载限制为十进制 1 GB；超过阈值或无法确认大小时停止，用户可以自行准备并显式指定本地模型目录。仓库不包含模型权重。

每次转写在 stderr 输出模型加载时间、推理时间、进程峰值 RSS 和 MLX 峰值分配。后两项存在统计重叠，不能相加；峰值还可能是同一进程的累计值。stdout 只输出转写文字。

## 实机验证

历史离线推理数据和各阶段验证范围见[本机验证记录](validation.md)。发布前应在自己的正常 macOS 会话中检查：

- 在 TextEdit、Chrome、Codex/Electron 与 VS Code 中，右 Option 开始／停止，组合输入不误触，浮层不改变输入焦点。
- 选区被转写文本替换；仅粘贴一次，不自动提交；中途切换应用时粘贴跟随当前焦点。
- Esc 取消、连续多轮、完整 5 分钟自动结束。
- 权限拒绝／恢复、修饰键未松开、休眠取消与模型加载失败后的重试。

当前还没有 `.app` 打包、签名、公证与开机自启流程；安装说明以仓库源码启动为准。
