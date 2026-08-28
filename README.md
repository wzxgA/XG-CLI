# XG-CLI

Python Agent CLI 。终端交互的 Agent 命令行工具，ReAct 直接执行 + `/plan` 计划模式（DAG 拆解、按轮执行）双路径，内置文件读写、代码搜索与命令执行工具。


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

`.env` 最小配置（单 provider，openai 为默认 provider）：

```
XG_OPENAI_API_KEY=sk-xxx       # 至少配置一个 provider 的专属 Key
XG_MODEL=gpt-4o-mini           # 可选
```

## 多 provider

内置 openai / deepseek / glm / kimi 四个 provider（均可走 OpenAI 兼容协议）。每个 provider 的 **URL 和 API Key 都能独立配置**，全部放 `.env`：

```
XG_PROVIDER=deepseek              # 激活哪个 provider
XG_OPENAI_API_BASE=https://api.openai.com/v1
XG_OPENAI_API_KEY=sk-xxx
XG_DEEPSEEK_API_BASE=https://api.deepseek.com/v1
XG_DEEPSEEK_API_KEY=sk-xxx
XG_GLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
XG_GLM_API_KEY=sk-xxx
XG_KIMI_API_BASE=https://api.moonshot.cn/v1
XG_KIMI_API_KEY=sk-xxx
```

Key 读取：每个 provider 必须配置自己的专属 `XG_<NAME>_API_KEY`（无通用兜底），占位值（`sk-xxx`）会被忽略。URL 优先级：专属 `XG_<NAME>_API_BASE` > 配置文件/内置预设（`XG_API_BASE` 仅对 openai 兼容生效）。

启动后运行时切换（无需重启）：

| 命令 | 行为 |
|------|------|
| `/model` | 列出所有 provider 与当前激活项 |
| `/model deepseek` | 切换到该 provider 的默认模型 |
| `/model glm/glm-4-plus` | 切换到指定模型 |
| `/model gpt-4o` | 当前 provider 内切换模型名 |

切换结果持久化到 `~/.xg/config.json`，重启后仍生效。配置优先级：环境变量/.env > 项目级 `.xg/config.json` > 用户级 `~/.xg/config.json` > 默认值。

## 使用

启动后直接输入任务，Agent 会自动调用工具完成多步操作（读目录 → 找文件 → 改内容 → 执行命令验证等）。

斜杠命令：

| 命令 | 说明 |
|------|------|
| `/plan <任务>` | 计划模式：先拆解为子任务 DAG，审阅后按轮执行（见下） |
| `/init` | 分析当前项目，预览并生成 `XG.md` 项目记忆（已有文件不覆盖） |
| `/save <内容>` | 显式保存一条当前项目长期记忆 |
| `/memory list\|search\|delete\|clear` | 管理当前项目的长期记忆 |
| `/model` | 切换 provider / 模型（见上） |
| `/config` | 显示当前生效配置（Key 脱敏） |
| `/config list` | provider 能力表 |
| `/config get <key>` | 查看配置项 |
| `/config set <key> <value>` | 设置并持久化到 `~/.xg/config.json` |
| `/mcp status` | 查看 MCP Server、工具和 resources 状态 |
| `/web status|providers|search|fetch` | 查看状态、搜索公开互联网或抓取公开网页 |
| `/mcp restart|logs|enable|disable|resources` | 管理 MCP Server |
| `/skill list` | 查看当前项目可用的 Skill 元信息 |
| `/skill load <name> [reference ...]` | 手动按需加载 Skill 和指定参考资料 |
| `/skill enable|disable <name>` | 启用或禁用 Skill |
| `/hitl` | 查看 HITL 审批状态 |
| `/hitl on\|off` | 开启 / 关闭危险操作审批 |
| `/clear` | 清空当前对话上下文 |
| `/exit` | 退出 |

## 计划模式

ReAct 之外的第二条执行路径。`/plan <任务>` 把多步任务先拆解为「子任务 + 依赖图」，经审阅后按依赖轮次执行：

1. **拆解**：LLM 独立调用生成结构化 JSON（子任务 + 依赖），自动校验与修复（JSON 解析失败带错误重试上限 2 次；未知依赖/自依赖自动移除；环检测；超上限截断）
2. **轮次生成**：Kahn 拓扑排序产出依赖轮次，无依赖子任务同轮并行
3. **审阅**：渲染计划面板后交互决策——`Enter` 执行 / `d` 展开子任务详情 / `r` 输入补充要求重规划 / `ESC`（或 `c`）取消（不执行任何工具）
4. **执行**：子任务以独立迷你 ReAct 循环执行（步数上限默认 10），依赖结果摘要注入下游上下文；子任务失败时错误注入依赖方让其自行调整，失败数超过上限（默认 3）终止剩余轮次
5. **汇总**：`plan_done` / `plan_failed` 面板展示各子任务状态与结果

子任务执行复用全部安全机制：并行工具、HITL 审批、策略层黑名单/路径越界拒绝、审计（含 `subtask_started` / `subtask_done` 事件）。

## 安全机制

- **并行执行**：模型一轮返回多个工具调用时并行执行（默认 4 并发），结果按原始顺序回灌
- **HITL 审批**：危险操作（默认 `execute_command` 必审、`write_file` 确认）执行前弹审批：`Enter` 批准 / `a` 本会话全部放行 / `r` 拒绝 / `s` 跳过 / `e` 改参后执行
- **策略层**：路径越界（PathGuard，含 symlink 逃逸）与黑名单命令（CommandGuard）直接拒绝，**不可被审批绕过**
- **审计日志**：所有工具调用/审批/拒绝记录到 `.xg/audit.log`（JSONL，敏感字段脱敏）

内置工具：`read_file` / `write_file` / `list_dir` / `glob_files` / `grep_code` / `execute_command` / `web_search` / `web_fetch` / `load_skill`（按配置启用）。

## Web 只读联网能力

提供 `web_search` 和 `web_fetch` 两个异步内置工具。搜索支持智谱、SerpAPI、SearXNG 三种 provider；抓取只允许公开 HTTP(S) 网页，逐跳校验 DNS 和重定向，拒绝 localhost、内网/保留 IP、非文本资源、超大响应和登录/动态页面。网页内容会标记为外部不可信资料，不会获得新的工具权限。

默认不启用搜索 provider，但 XG 仍可启动；抓取不依赖搜索配置。可通过环境变量或 `.xg/web.json` 配置，常用变量见 `.env.example`。命令行中使用 `/web status`、`/web providers`、`/web search <query>` 和 `/web fetch <url>`。

## Skill 技能系统

Skill 是可发现、按需加载的本地任务规范，不是脚本、插件或新的执行权限。XG 启动时只扫描 `SKILL.md` 的名称和描述并注入有限索引；Agent 或用户执行 `/skill load <name>` 后，才读取正文和明确指定的 `references/` 文件。Skill 中的文字仍是补充资料，不能覆盖系统提示、安全策略、HITL 或工具权限。

Skill 目录按优先级从低到高合并：内置 `xg/skills/`、用户级 `~/.xg/skills/`、项目级 `<project>/.xg/skills/`。同名 Skill 由高层完整覆盖。用户/项目启用状态保存在对应层的 `skills.json`，常用命令为 `/skill list`、`/skill load <name>`、`/skill enable <name>` 和 `/skill disable <name>`。默认的索引、正文和 reference 大小限制见 `.env.example`；`XG_SKILLS_ENABLED=off` 时不会注册 `load_skill`，其他工具仍可用。

## MCP 外部能力

XG 可以通过 MCP 接入外部工具和 resources，支持本地 `stdio` 子进程与 `Streamable HTTP`。用户级配置位于 `~/.xg/mcp.json`，项目级配置位于 `.xg/mcp.json`；同名 Server 由项目配置覆盖，敏感值使用 `${VAR}` 从环境变量或 `.env` 展开。

```json
{
  "servers": {
    "local_docs": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "my_docs_mcp"],
      "env": {"DOCS_TOKEN": "${DOCS_TOKEN}"}
    },
    "remote": {
      "transport": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
    }
  }
}
```

Server 工具会动态注册为 `mcp__{server}__{tool}`，默认经过 HITL 确认并写入 `.xg/audit.log`。resources 可由 Agent 通过虚拟 list/read 工具读取，也可以在输入中显式引用：

```text
根据 @local_docs:file:///specs/api.md 检查当前实现
```

常用管理命令：`/mcp status`、`/mcp restart <server>`、`/mcp logs <server>`、`/mcp enable <server>`、`/mcp disable <server>`、`/mcp resources [server]`。MCP Server 是外部代码/服务，只应启用可信配置；不要把真实 token 直接写入 `mcp.json`。

## 记忆与上下文

- 项目根目录的 `XG.md`（共享）和 `XG.local.md`（本地可选）会自动注入每次任务；运行中修改后下一次顶层任务自动热加载。
- `/save <内容>` 将用户明确提供的内容保存到项目 `.xg/memory.db`，不会自动保存普通聊天；`/clear` 不会清除长期记忆。
- 长对话接近上下文预算时会自动压缩较旧的完整对话轮次，保留最近轮次和工具调用关系；无法安全压缩时才停止并提示。
- `.xg/memory.db` 是本地明文数据库，项目记忆会发送给当前 LLM provider。不要在 `XG.md` 或 `/save` 中放置 API Key、密码等敏感信息。

## 全屏 TUI

`/plan` 生成的计划直接以内嵌 `PlanCard` 出现在对话流中，不打开新的审阅界面。审阅期间普通输入禁用；`Enter` 执行、`d` 展开或折叠任务详情、`r` 输入重规划要求、`Esc` 取消计划。

使用 Textual 将 inline CLI 升级为全屏终端界面：

- Header：provider、model、上下文比例、HITL 和任务状态
- Transcript：流式 Markdown、工具调用卡片、错误和计划进度
- Composer：多行输入、命令补全、历史和快捷键
- Modal：HITL 审批、`/init` 与记忆清空确认；Plan 使用对话内嵌卡片审阅
- Inspector：Session、Plan、Memory、Safety 状态面板

当前入口为 `xg` 默认全屏、`xg --inline` 保留兼容模式，并支持 `xg --tui` 强制全屏、`xg --no-tui` 兼容 inline。全屏 TUI 不改变 ReAct、Plan、Memory、ToolRegistry 或安全策略核心；非交互终端仍使用 inline fallback。

## 配置项

| 环境变量 | 说明 |
|----------|------|
| `XG_PROVIDER` | 激活的 provider（openai / deepseek / glm / kimi 或自定义），优先于配置文件 |
| `XG_<NAME>_API_BASE` | 各 provider 专属 URL，如 `XG_DEEPSEEK_API_BASE` |
| `XG_<NAME>_API_KEY` | 各 provider 专属 Key（必配，无通用兜底），如 `XG_DEEPSEEK_API_KEY` |
| `XG_API_BASE` | 旧键兼容，仅对 openai 生效 |
| `XG_MODEL` | 默认模型（未配置 active_model 时生效） |
| `XG_CONTEXT_WINDOW` | 上下文窗口（token），覆盖 provider 能力声明 |
| `XG_CONTEXT_BUDGET_RATIO` | 自动压缩前的输入预算比例（默认 0.8，限制 0.5~0.9） |
| `XG_CONTEXT_KEEP_RECENT_TURNS` | 自动压缩保留的最近完整对话轮次（默认 4） |
| `XG_CONTEXT_SUMMARY_MAX_TOKENS` | 摘要输出动态预留上限（默认 4096） |
| `XG_MEMORY_PROMPT_MAX_CHARS` | 自动注入长期记忆的字符上限（默认 8000） |
| `XG_PROJECT_MEMORY_MAX_CHARS` | 单个项目记忆文件读取上限（默认 32000） |
| `XG_TOOL_STEPS` | 单轮工具调用步数上限（默认 20） |
| `XG_MAX_PARALLEL` | 并行工具执行并发数（默认 4） |
| `XG_TOOL_TIMEOUT` | 单工具执行超时秒数（默认 120） |
| `XG_HITL` | 危险操作审批开关（on 默认 / off 危险模式） |
| `XG_PLAN_MAX_SUBTASKS` | 计划模式子任务数上限（默认 12，超出截断） |
| `XG_PLAN_SUBTASK_STEPS` | 计划模式单个子任务最大工具步数（默认 10） |
| `XG_PLAN_MAX_FAILURES` | 计划级允许失败数（默认 3，超出终止剩余轮次） |
| `XG_MCP_ENABLED` | MCP 总开关（on 默认） |
| `XG_MCP_STARTUP_TIMEOUT` | MCP Server 初始化超时秒数（默认 15） |
| `XG_MCP_REQUEST_TIMEOUT` | MCP 单请求超时秒数（默认 120） |
| `XG_MCP_MAX_SERVERS` | MCP Server 数量上限（默认 32） |
| `XG_MCP_MAX_TOOLS` | 每个 Server 工具数上限（默认 256） |
| `XG_MCP_MAX_RESOURCES` | 每个 Server resource 数上限（默认 512） |
| `XG_MCP_RESOURCE_MAX_CHARS` | 单 resource 文本上限（默认 32000） |
| `XG_WEB_ENABLED` | Web 工具总开关（默认 on） |
| `XG_WEB_SEARCH_PROVIDER` | 搜索 provider：none / zhipu / serpapi / searxng |
| `XG_WEB_TIMEOUT` | 搜索/抓取超时秒数（默认 15） |
| `XG_WEB_MAX_RESPONSE_BYTES` | 单网页响应字节上限（默认 2 MiB） |
| `XG_WEB_FETCH_MAX_CHARS` | 单网页正文字符上限（默认 32000） |
| `XG_WEB_MAX_REDIRECTS` | 最大重定向次数（默认 5） |
| `XG_WEB_RATE_LIMIT_PER_MINUTE` | 每类 Web 调用每分钟上限（默认 30） |
| `XG_SKILLS_ENABLED` | Skill 总开关（默认 on） |
| `XG_SKILLS_MAX_INDEX_ITEMS` | system prompt 最多展示的 Skill 数（默认 20） |
| `XG_SKILLS_MAX_INDEX_CHARS` | Skill 索引字符上限（默认 4096） |
| `XG_SKILLS_MAX_CHARS` | 单个 Skill 正文字符上限（默认 32000） |
| `XG_SKILLS_MAX_REFERENCE_CHARS` | 单个 reference 字符上限（默认 16000） |
| `XG_SKILLS_MAX_LOADED_CHARS` | 单次 Skill 加载总字符上限（默认 64000） |

API Key 只从环境变量 / .env 读取，不写入配置文件；`/config` 显示时脱敏。

## 开发

```bash
uv run pytest -m "not slow"   # 常规回归
uv run pytest                 # 全量测试
uv run xg                     # 手工验收
```

项目分层：`xg/agent`（ReAct 循环 + 计划模式）、`xg/llm`（客户端抽象 + OpenAI 兼容实现 + 工厂）、`xg/tool`（统一工具注册表 + 内置工具）、`xg/mcp`（协议、transport、动态工具和 resources）、`xg/skill`（Skill 发现、解析、按需加载与安全策略）、`xg/memory`（项目/长期记忆 + 上下文压缩）、`xg/tui`（Textual 全屏交互层）、`xg/cli`（入口与 inline fallback）、`xg/config`（provider/MCP/Web/Skill 配置与运行时快照）。
