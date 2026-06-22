# XG-CLI

Python Agent CLI 。终端交互的 Agent 命令行工具，ReAct 直接执行 + `/plan` 计划模式（DAG 拆解、批次执行）双路径，内置文件读写、代码搜索与命令执行工具。


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
| `/plan <任务>` | 计划模式：先拆解为子任务 DAG，审阅后按批次执行（见下） |
| `/model` | 切换 provider / 模型（见上） |
| `/config` | 显示当前生效配置（Key 脱敏） |
| `/config list` | provider 能力表 |
| `/config get <key>` | 查看配置项 |
| `/config set <key> <value>` | 设置并持久化到 `~/.xg/config.json` |
| `/hitl` | 查看 HITL 审批状态 |
| `/hitl on\|off` | 开启 / 关闭危险操作审批 |
| `/clear` | 清空当前对话上下文 |
| `/exit` | 退出 |

## 计划模式（第 4 期）

ReAct 之外的第二条执行路径。`/plan <任务>` 把多步任务先拆解为「子任务 + 依赖图」，经审阅后按依赖批次执行：

1. **拆解**：LLM 独立调用生成结构化 JSON（子任务 + 依赖），自动校验与修复（JSON 解析失败带错误重试上限 2 次；未知依赖/自依赖自动移除；环检测；超上限截断）
2. **批次生成**：Kahn 拓扑排序产出依赖批次，无依赖子任务同批并行
3. **审阅**：渲染计划面板后交互决策——`Enter` 执行 / `d` 展开子任务详情 / `r` 输入补充要求重规划 / `ESC`（或 `c`）取消（不执行任何工具）
4. **执行**：子任务以独立迷你 ReAct 循环执行（步数上限默认 10），依赖结果摘要注入下游上下文；子任务失败时错误注入依赖方让其自行调整，失败数超过上限（默认 3）终止剩余批次
5. **汇总**：`plan_done` / `plan_failed` 面板展示各子任务状态与结果

子任务执行复用第 3 期全部安全机制：并行工具、HITL 审批、策略层黑名单/路径越界拒绝、审计（含 `subtask_started` / `subtask_done` 事件）。

## 安全机制（第 3 期）

- **并行执行**：模型一轮返回多个工具调用时并行执行（默认 4 并发），结果按原始顺序回灌
- **HITL 审批**：危险操作（默认 `execute_command` 必审、`write_file` 确认）执行前弹审批：`Enter` 批准 / `a` 本会话全部放行 / `r` 拒绝 / `s` 跳过 / `e` 改参后执行
- **策略层**：路径越界（PathGuard，含 symlink 逃逸）与黑名单命令（CommandGuard）直接拒绝，**不可被审批绕过**
- **审计日志**：所有工具调用/审批/拒绝记录到 `.xg/audit.log`（JSONL，敏感字段脱敏）

内置工具：`read_file` / `write_file` / `list_dir` / `glob_files` / `grep_code` / `execute_command`。

## 配置项

| 环境变量 | 说明 |
|----------|------|
| `XG_PROVIDER` | 激活的 provider（openai / deepseek / glm / kimi 或自定义），优先于配置文件 |
| `XG_<NAME>_API_BASE` | 各 provider 专属 URL，如 `XG_DEEPSEEK_API_BASE` |
| `XG_<NAME>_API_KEY` | 各 provider 专属 Key（必配，无通用兜底），如 `XG_DEEPSEEK_API_KEY` |
| `XG_API_BASE` | 旧键兼容，仅对 openai 生效 |
| `XG_MODEL` | 默认模型（未配置 active_model 时生效） |
| `XG_CONTEXT_WINDOW` | 上下文窗口（token），覆盖 provider 能力声明 |
| `XG_TOOL_STEPS` | 单轮工具调用步数上限（默认 20） |
| `XG_MAX_PARALLEL` | 并行工具执行并发数（默认 4） |
| `XG_TOOL_TIMEOUT` | 单工具执行超时秒数（默认 120） |
| `XG_HITL` | 危险操作审批开关（on 默认 / off 危险模式） |
| `XG_PLAN_MAX_SUBTASKS` | 计划模式子任务数上限（默认 12，超出截断） |
| `XG_PLAN_SUBTASK_STEPS` | 计划模式单个子任务最大工具步数（默认 10） |
| `XG_PLAN_MAX_FAILURES` | 计划级允许失败数（默认 3，超出终止剩余批次） |

API Key 只从环境变量 / .env 读取，不写入配置文件；`/config` 显示时脱敏。

## 开发

```bash
uv run pytest -m "not slow"   # 常规回归
uv run pytest                 # 全量测试
uv run xg                     # 手工验收
```

项目分层：`xg/agent`（ReAct 循环 + 计划模式）、`xg/llm`（客户端抽象 + OpenAI 兼容实现 + 工厂）、`xg/tool`（工具注册表 + 内置工具）、`xg/cli`（交互层）、`xg/config`（provider 注册表 / 配置合并 / 运行时快照）。
