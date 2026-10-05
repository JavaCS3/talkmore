# 贡献指南

欢迎提交问题或改进。较大功能请先在 Issue 中说明使用场景；小修复可直接提交 Pull Request。

## 本地开发

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
```

运行要求和代码结构见[开发说明](docs/development.md)。修改依赖时同步更新 `pyproject.toml` 与 `uv.lock`。修改权限、快捷键或粘贴行为时，请说明已完成的实机验证和仍需验证的部分。

## 报告问题

请提供 macOS 版本、Apple Silicon 芯片、启动命令、模型选项、复现步骤、预期／实际行为，以及终端或菜单中的错误。涉及权限时附上权限状态和启动终端名称。

提交前移除私人转写、录音、账号凭据与其他敏感内容。模型权重、环境缓存和录音不应加入仓库。

## Pull Request

说明具体问题、行为变化和验证方式。保持改动集中，沿用现有 unittest 测试方式；有行为变化时增加相应回归测试。

贡献的 Talkmore 代码按仓库的 [MIT License](LICENSE) 提供；引入第三方代码或资源时保留其许可证和来源。
