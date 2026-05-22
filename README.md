# XG-CLI

Python Agent CLI 。终端交互的 Agent 命令行工具，ReAct 模式驱动，内置文件读写、代码搜索与命令执行工具。


## 快速开始

要求：Python 3.11+，[uv](https://docs.astral.sh/uv/)。

```bash
# 安装依赖
uv sync

# 配置 API（复制示例并填写）
cp .env.example .env

# 启动
uv run xg
```

`.env` 最小配置：

```
XG_API_BASE=https://api.openai.com/v1   # 任意 OpenAI 兼容服务
XG_API_KEY=sk-xxx
XG_MODEL=gpt-4o-mini
```

## 使用

启动后直接输入任务，Agent 会自动调用工具完成多步操作（读目录 → 找文件 → 改内容 → 执行命令验证等）。

斜杠命令：

| 命令 | 说明 |
|------|------|
| `/model <name>` | 运行时切换模型 |
| `/clear` | 清空当前对话上下文 |
| `/exit` | 退出 |

内置工具：`read_file` / `write_file` / `list_dir` / `glob_files` / `grep_code` / `execute_command`。命令执行记录写入 `.xg/audit.log`。

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `XG_API_BASE` | - | OpenAI 兼容 API 地址 |
| `XG_API_KEY` | - | API Key |
| `XG_MODEL` | - | 模型名 |
| `XG_CONTEXT_WINDOW` | 128000 | 上下文窗口（token），用于预算控制 |
| `XG_TOOL_STEPS` | 20 | 单轮工具调用步数上限 |

## 开发

```bash
uv run pytest -m "not slow"   # 常规回归
uv run pytest                 # 全量测试
uv run xg                     # 手工验收
```

项目分层：`xg/agent`（ReAct 循环）、`xg/llm`（客户端抽象 + OpenAI 兼容实现）、`xg/tool`（工具注册表 + 内置工具）、`xg/cli`（交互层）、`xg/config`（配置）。
