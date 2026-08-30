# XG-CLI 技术栈报告

> 生成方式：基于 `pyproject.toml`、`uv.lock` 及源码 import 证据交叉验证整理。
> 项目定位：**纯终端运行的商业级 Python Agent CLI/TUI 工具**（非 Web 服务，无前端）。

## 1. 编程语言

| 语言 | 说明 |
|---|---|
| **Python** | 唯一源码语言，要求 **>= 3.11**（`pyproject.toml` `requires-python`）。源码约 102 个 `.py` 文件（`xg/` 包）+ 52 个测试 `.py` 文件（`tests/`），无其他语言源码。 |

## 2. 框架与关键库

### 2.1 运行时依赖（5 个直接依赖）

| 依赖 | 最低版本 | 用途 | 源码证据 |
|---|---|---|---|
| **textual** | 0.70.0 | TUI 框架（全屏交互界面） | `xg/tui/app.py`：`from textual.app import App, ComposeResult`；`xg/tui/widgets/approval_modal.py` 等 16 个 widget |
| **prompt-toolkit** | 3.0.43 | CLI 交互框架（输入会话/快捷键） | `xg/cli/app.py`：`PromptSession`、`KeyBindings`、`HTML` |
| **rich** | 13.7.0 | 终端富文本渲染（表格/面板/Markdown） | `xg/cli/app.py`：`Console/Live/Markdown/Panel/Table/Text`；`xg/tui/renderables.py` |
| **httpx** | 0.27.0 | HTTP 客户端（LLM API / MCP 传输 / Web 搜索抓取） | `xg/llm/openai_compat.py`（OpenAI 兼容 API）；`xg/mcp/http.py`（streamable HTTP）；`xg/web/fetch.py`、`search.py` |
| **python-dotenv** | 1.0.1 | 环境变量 / `.env` 加载 | 配置模块（`xg/config/`） |

### 2.2 自实现关键模块（无第三方 SDK）

| 模块 | 说明 |
|---|---|
| MCP 协议 | `xg/mcp/` 手写实现（protocol / transport / http / stdio），未使用官方 mcp SDK |
| LLM 客户端 | `xg/llm/openai_compat.py` 基于 httpx 直接实现 OpenAI 兼容 Chat Completions（SSE 流式 + tool calling），未使用 openai SDK |
| 数据模型 | 全部使用标准库 `dataclasses`（如 `xg/agent/plan.py`），未引入 pydantic |

### 2.3 明确排除项

- ❌ 无 Web 服务端框架：Django / Flask / FastAPI / Starlette / uvicorn（全项目无相关 import）
- ❌ 无前端框架：React / Vue / Next.js 等（项目为纯终端应用）
- ❌ 无其他语言生态库

## 3. 依赖管理

| 项 | 值 |
|---|---|
| 依赖管理器 | **uv**（Astral），锁文件 `uv.lock`（lockfile v1, revision 3，329 行，锁定完整依赖树含传递依赖与哈希，PyPI registry） |
| 依赖清单 | 唯一清单 `pyproject.toml`，采用 `[dependency-groups]` 语法（无 `requirements*.txt` / `setup.py`） |
| 运行时依赖 | 5 个（见上文 2.1） |
| 开发依赖 | 3 个：`pytest>=8.0.0`、`pytest-asyncio>=0.24.0`、`respx>=0.21.1`（HTTP mock，配合 httpx 测试） |
| 测试配置 | `pytest` 启用 `asyncio_mode = "auto"`，含 `slow` 标记（`uv run pytest -m slow`） |

## 4. 构建工具

| 项 | 值 |
|---|---|
| 构建后端 | **hatchling**（`[build-system]`：`build-backend = "hatchling.build"`） |
| 打包目标 | wheel，`packages = ["xg"]` |
| 命令行入口 | `xg = "xg.cli.app:main"`（`[project.scripts]`） |
| 安装/运行方式 | `uv sync` 安装依赖 → `uv run xg` 启动（见 `XG-docs/howtouse/README.md`） |

## 5. 部署方式

**无任何部署配置**。项目是本地终端 CLI/TUI 工具，不提供容器化、CI/CD、云平台或发行部署：

- ❌ 无 `Dockerfile` / `docker-compose.yml`、`Makefile`、`Jenkinsfile`、`CMakeLists.txt`
- ❌ 无 `.github/workflows/`、`.devcontainer/`、`scripts/`、`ci/` 目录
- ❌ 无 `*.sh` / `*.service` / `*.spec` 等脚本或服务配置

## 6. 依赖树概览（uv.lock 中的主要传递依赖）

`uv.lock` 锁定的传递依赖（节选）：`anyio 4.14.2`、`certifi`、`colorama 0.4.6`、`idna`、`typing-extensions` 等，均来自 PyPI。完整锁定版本与哈希以 `uv.lock` 为准。
