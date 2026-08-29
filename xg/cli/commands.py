"""Commands shared by the inline CLI and the fullscreen UI.

The command service deliberately returns data instead of printing it.  The
legacy helpers in :mod:`xg.cli.app` remain the compatibility implementation
for now; keeping this adapter small lets the TUI use the exact same command
semantics while the inline renderer is migrated incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xg.tui.i18n import normalize_language


@dataclass
class CommandContext:
    agent: Any
    settings: Any
    manager: Any


@dataclass
class CommandResult:
    ok: bool
    message: str = ""
    should_exit: bool = False
    open_modal: str = ""
    data: object | None = None


@dataclass(frozen=True)
class SlashSubcommandSpec:
    """Help metadata for one command mode or subcommand."""

    name: str
    usage: str
    description: str


@dataclass(frozen=True)
class SlashCommandSpec:
    """Read-only metadata shared by command help and TUI suggestions."""

    name: str
    aliases: tuple[str, ...] = ()
    usage: str = ""
    description: str = ""
    category: str = "general"
    details: tuple[str, ...] = ()
    subcommands: tuple[SlashSubcommandSpec, ...] = ()
    examples: tuple[str, ...] = ()


# Keep this tuple in presentation order.  It is intentionally metadata only;
# command execution remains in CommandService and the legacy compatibility
# helpers in xg.cli.app.
SLASH_COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec(
        "/plan",
        usage="/plan <任务>",
        description="生成、审阅并执行计划",
        category="workflow",
        details=("先生成任务依赖图，审阅通过后再按依赖轮次执行。",),
        examples=("/plan 检查项目配置并修复测试",),
    ),
    SlashCommandSpec(
        "/model",
        usage="/model [provider] [model]",
        description="查看或切换 provider / 模型",
        category="config",
        details=(
            "不带参数时查看当前模型和可用 provider。",
            "可以只切换 provider、切换 provider 与模型，或只切换当前 provider 内的模型。",
        ),
        subcommands=(
            SlashSubcommandSpec("list", "/model", "查看当前模型和可用 provider"),
            SlashSubcommandSpec("provider", "/model <provider>", "切换到 provider 的默认模型"),
            SlashSubcommandSpec("provider/model", "/model <provider>/<model>", "切换 provider 和指定模型"),
            SlashSubcommandSpec("model", "/model <model>", "在当前 provider 内切换模型"),
        ),
        examples=(
            "/model",
            "/model deepseek",
            "/model deepseek/deepseek-chat",
            "/model gpt-4o",
        ),
    ),
    SlashCommandSpec(
        "/config",
        usage="/config get|set ...",
        description="查看或修改配置",
        category="config",
        details=(
            "不带参数时查看当前生效配置，API Key 会脱敏。",
            "`set` 的 value 当前按单个空格分隔参数解析。",
        ),
        subcommands=(
            SlashSubcommandSpec("overview", "/config", "查看当前生效配置"),
            SlashSubcommandSpec("list", "/config list", "查看 provider 能力列表"),
            SlashSubcommandSpec("get", "/config get <key>", "查看指定配置项"),
            SlashSubcommandSpec("set", "/config set <key> <value>", "修改并持久化配置"),
        ),
        examples=(
            "/config",
            "/config list",
            "/config get active_provider",
            "/config set active_model gpt-4o",
        ),
    ),
    SlashCommandSpec(
        "/mcp",
        usage="/mcp status|restart|logs|enable|disable|resources",
        description="管理 MCP Server",
        category="config",
        details=("省略子命令时默认查看 Server 状态。",),
        subcommands=(
            SlashSubcommandSpec("status", "/mcp status", "查看 Server、工具和资源状态"),
            SlashSubcommandSpec("restart", "/mcp restart <server>", "重启并重新发现指定 Server"),
            SlashSubcommandSpec("logs", "/mcp logs <server>", "查看指定 Server 的脱敏日志"),
            SlashSubcommandSpec("enable", "/mcp enable <server>", "启用指定 Server"),
            SlashSubcommandSpec("disable", "/mcp disable <server>", "禁用指定 Server"),
            SlashSubcommandSpec("resources", "/mcp resources [server]", "查看全部或指定 Server 的 resources"),
        ),
        examples=(
            "/mcp status",
            "/mcp restart local",
            "/mcp logs local",
            "/mcp resources",
        ),
    ),
    SlashCommandSpec(
        "/web",
        usage="/web status|providers|search|fetch",
        description="查看或使用只读联网能力",
        category="config",
        details=(
            "搜索和抓取均为只读能力；搜索需要配置 provider，抓取接受公开 HTTP(S) URL。",
            "`search` 和 `fetch` 的参数分别作为 query 和 URL 传递。",
        ),
        subcommands=(
            SlashSubcommandSpec("status", "/web status", "查看联网开关、provider 和抓取限制"),
            SlashSubcommandSpec("providers", "/web providers", "查看搜索 provider 配置状态"),
            SlashSubcommandSpec("search", "/web search <query>", "搜索公开互联网"),
            SlashSubcommandSpec("fetch", "/web fetch <url>", "抓取公开网页正文"),
        ),
        examples=(
            "/web status",
            "/web providers",
            "/web search Python 3.13 新特性",
            "/web fetch https://www.example.com/",
        ),
    ),
    SlashCommandSpec(
        "/skill",
        usage="/skill list|load|enable|disable",
        description="管理任务 Skill",
        category="config",
        details=(
            "Skill 是本地任务规范；加载 Skill 不会新增工具权限，也不能覆盖系统提示和安全策略。",
            "`reference` 为可选的指定参考资料路径或名称。",
        ),
        subcommands=(
            SlashSubcommandSpec("list", "/skill list", "查看可用 Skill 元信息"),
            SlashSubcommandSpec("load", "/skill load <name> [reference ...]", "按需加载 Skill 和参考资料"),
            SlashSubcommandSpec("enable", "/skill enable <name>", "启用指定 Skill"),
            SlashSubcommandSpec("disable", "/skill disable <name>", "禁用指定 Skill"),
        ),
        examples=(
            "/skill list",
            "/skill load code-review",
            "/skill load code-review references/style.md",
            "/skill disable code-review",
        ),
    ),
    SlashCommandSpec(
        "/history",
        usage="/history status|clear",
        description="查看或清理输入历史",
        category="session",
        details=("命令只管理本地输入历史，不显示历史全文，也不影响 Agent 对话和长期记忆。",),
        subcommands=(
            SlashSubcommandSpec("status", "/history status", "查看输入历史状态和数量"),
            SlashSubcommandSpec("clear", "/history clear", "清理当前项目的输入历史"),
        ),
        examples=("/history status", "/history clear"),
    ),
    SlashCommandSpec(
        "/memory",
        usage="/memory list|search|delete|clear",
        description="管理长期记忆",
        category="memory",
        details=(
            "长期记忆按当前项目隔离保存；普通对话不会自动写入长期记忆。",
            "`clear` 会要求确认，`delete` 需要使用记忆条目的数字 ID。",
        ),
        subcommands=(
            SlashSubcommandSpec("list", "/memory list [limit]", "列出长期记忆，可选限制条数"),
            SlashSubcommandSpec("search", "/memory search <关键词>", "按关键词搜索长期记忆"),
            SlashSubcommandSpec("delete", "/memory delete <id>", "删除指定 ID 的长期记忆"),
            SlashSubcommandSpec("clear", "/memory clear", "清空全部长期记忆并请求确认"),
        ),
        examples=(
            "/memory list",
            "/memory list 10",
            "/memory search 发布流程",
            "/memory delete 3",
            "/memory clear",
        ),
    ),
    SlashCommandSpec(
        "/lang",
        aliases=("/language",),
        usage="/lang [en|zh|reset]",
        description="查看或切换 Inspector language",
        category="session",
        details=(
            "默认语言为 English；切换只影响 TUI 右侧 Inspector，不影响 Agent 对话。",
            "reset 会清除用户级语言偏好并恢复 English。",
        ),
        examples=("/lang", "/lang zh", "/lang en", "/lang reset"),
    ),
    SlashCommandSpec("/help", aliases=("/?",), usage="/help", description="查看命令帮助", category="general"),
    SlashCommandSpec(
        "/init",
        usage="/init",
        description="初始化项目记忆",
        category="memory",
        details=("分析当前项目并生成 XG.md 草稿；写入前会请求确认，已有文件不会直接覆盖。",),
        examples=("/init",),
    ),
    SlashCommandSpec(
        "/save",
        usage="/save <内容>",
        description="保存长期记忆",
        category="memory",
        details=("只保存用户明确提供的内容；敏感片段会按现有记忆策略脱敏。",),
        examples=("/save 项目使用 Python 3.13，测试命令是 uv run pytest",),
    ),
    SlashCommandSpec(
        "/hitl",
        usage="/hitl on|off|reset",
        description="管理人工审批开关",
        category="safety",
        details=(
            "HITL 用于危险工具调用的人工审批。关闭后危险操作不再弹出审批，属于高风险模式。",
        ),
        subcommands=(
            SlashSubcommandSpec("status", "/hitl", "查看当前审批开关和本会话放行状态"),
            SlashSubcommandSpec("on", "/hitl on", "开启危险操作审批"),
            SlashSubcommandSpec("off", "/hitl off", "关闭危险操作审批并重置放行状态"),
            SlashSubcommandSpec("reset", "/hitl reset", "清除本会话全部放行状态"),
        ),
        examples=("/hitl", "/hitl on", "/hitl reset"),
    ),
    SlashCommandSpec("/clear", usage="/clear", description="清空当前上下文", category="session", examples=("/clear",)),
    SlashCommandSpec("/cancel", aliases=("/c",), usage="/cancel", description="取消当前任务", category="control", examples=("/cancel",)),
    SlashCommandSpec("/exit", aliases=("/quit",), usage="/exit", description="退出程序", category="control", examples=("/exit",)),
)


def filter_slash_commands(query: str) -> tuple[SlashCommandSpec, ...]:
    """Return stable, prefix-matched command specs for a top-level token."""

    normalized = query.strip().lower()
    if not normalized.startswith("/") or any(char.isspace() for char in normalized):
        return ()

    # An exact alias is an unambiguous command selection. For example, `/c`
    # is the cancel alias even though it is also a prefix of `/config` and
    # `/clear`.
    exact_alias = next(
        (
            spec
            for spec in SLASH_COMMANDS
            if any(alias.lower() == normalized for alias in spec.aliases)
        ),
        None,
    )
    if exact_alias is not None:
        return (exact_alias,)

    matches = [
        (index, spec)
        for index, spec in enumerate(SLASH_COMMANDS)
        if spec.name.lower().startswith(normalized)
        or any(alias.lower().startswith(normalized) for alias in spec.aliases)
    ]
    matches.sort(
        key=lambda pair: (
            0
            if pair[1].name.lower() == normalized
            or any(alias.lower() == normalized for alias in pair[1].aliases)
            else 1,
            pair[0],
        )
    )
    return tuple(spec for _, spec in matches)


class CommandService:
    """Execute slash commands without knowing anything about Textual."""

    def __init__(self, context: CommandContext) -> None:
        self.context = context

    async def execute(self, raw: str) -> CommandResult:
        raw = raw.strip()
        if not raw:
            return CommandResult(ok=False, message="命令不能为空")
        if raw.lower() in ("/cancel", "/c"):
            return CommandResult(ok=True, message="已请求取消当前任务")

        parts = raw.split(maxsplit=1)
        if parts[0].lower() in ("/help", "/?"):
            from xg.cli.help import format_command_help, format_help

            query = parts[1].strip() if len(parts) > 1 else ""
            return CommandResult(
                ok=True,
                message=format_command_help(query) if query else format_help(),
            )

        if parts[0].lower() in ("/lang", "/language"):
            return _execute_language_command(self.context.settings, self.context.manager, raw)

        if parts[0].lower() == "/mcp":
            message, ok = await execute_mcp_command(self.context.agent, raw)
            return CommandResult(ok=ok, message=message)
        if parts[0].lower() == "/web":
            message, ok = await execute_web_command(self.context.agent, raw)
            return CommandResult(ok=ok, message=message)
        if parts[0].lower() == "/skill":
            message, ok = await execute_skill_command(self.context.agent, raw)
            return CommandResult(ok=ok, message=message)
        if parts[0].lower() == "/history":
            message, ok = await execute_history_command(self.context.agent, raw)
            return CommandResult(ok=ok, message=message)

        # Lazy import avoids a cycle: app.py still owns the legacy renderer
        # and its command helpers are kept as the public compatibility API.
        from xg.cli.app import _handle_command, _handle_memory_command

        cmd = raw.split(maxsplit=1)[0].lower()
        if cmd == "/init":
            # Generation and confirmation are UI concerns.  The controller
            # handles this command separately so a TUI can show a modal.
            return CommandResult(ok=True, open_modal="init", message="正在准备项目记忆草稿")

        if cmd in ("/save", "/memory"):
            message, should_exit = _handle_command(
                self.context.agent, self.context.settings, self.context.manager, raw
            )
            return CommandResult(ok=not should_exit, message=message or "", should_exit=should_exit)

        message, should_exit = _handle_command(
            self.context.agent, self.context.settings, self.context.manager, raw
        )
        return CommandResult(ok=not (message and message.startswith("未知命令")), message=message or "", should_exit=should_exit)


def _execute_language_command(settings: Any, manager: Any, raw: str) -> CommandResult:
    """Handle the UI-only language preference without touching the Agent."""
    parts = raw.split()
    current = normalize_language(getattr(settings, "ui_language", "en"))
    if len(parts) == 1:
        source = "default"
        if hasattr(manager, "ui_language_source"):
            source = manager.ui_language_source()
        return CommandResult(
            ok=True,
            message=(
                f"Inspector language: {current} (source: {source})\n"
                "Available: en, zh\n"
                "Usage: /lang en|zh|reset"
            ),
            data={"ui_language": current, "persisted": True},
        )
    if len(parts) != 2 or parts[1].lower() not in {"en", "zh", "reset"}:
        return CommandResult(
            ok=False,
            message="Usage: /lang [en|zh|reset]",
            data={"ui_language": current, "persisted": False},
        )

    requested = parts[1].lower()
    language = "en" if requested == "reset" else requested
    persisted = True
    try:
        if requested == "reset":
            manager.reset_ui_language()
        else:
            manager.set_ui_language(language)
    except (AttributeError, OSError, ValueError) as exc:
        # A read-only or broken config directory must not prevent a session
        # from changing its presentation language.
        persisted = False
        warning = f" (session only; could not persist: {exc})"
    else:
        warning = ""

    settings.ui_language = language
    action = "reset to English" if requested == "reset" else f"changed to {language}"
    return CommandResult(
        ok=True,
        message=f"Inspector language {action}.{warning}",
        data={"ui_language": language, "persisted": persisted},
    )


async def execute_mcp_command(agent: Any, raw: str) -> tuple[str, bool]:
    """Execute the shared asynchronous /mcp command group."""
    manager = getattr(agent, "mcp_manager", None)
    if manager is None:
        return "MCP 未初始化。", False
    await manager.ensure_started()
    parts = raw.split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    name = parts[2] if len(parts) > 2 else ""
    if sub == "status":
        return manager.format_status(), True
    if sub == "resources":
        return manager.format_resources(name or None), True
    if sub == "logs":
        if not name:
            return "用法: /mcp logs <server>", False
        return manager.logs(name), name in {item.name for item in manager.snapshots()}
    if sub == "restart":
        if not name:
            return "用法: /mcp restart <server>", False
        ok = await manager.restart(name)
        return (f"MCP Server {name} 已重启。" if ok else f"MCP Server {name} 重启失败或不存在。"), ok
    if sub in {"enable", "disable"}:
        if not name:
            return f"用法: /mcp {sub} <server>", False
        enabled = sub == "enable"
        ok = await manager.set_enabled(name, enabled)
        action = "启用" if enabled else "禁用"
        return (f"MCP Server {name} 已{action}。" if ok else f"MCP Server {name} {action}失败或不存在。"), ok
    return "用法: /mcp status|restart <server>|logs <server>|enable <server>|disable <server>|resources [server]", False


async def execute_web_command(agent: Any, raw: str) -> tuple[str, bool]:
    """Shared read-only Web command semantics for inline and TUI."""
    config = getattr(agent, "web_config", None)
    parts = raw.split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "status"
    if config is None:
        return "Web 能力未初始化。", False
    if sub in {"status", ""}:
        search = config.search
        configured = bool(search.api_base and (search.api_key or search.provider == "searxng"))
        return (f"Web: {'启用' if config.enabled else '关闭'}\n"
                f"search provider: {search.provider}（{'已配置' if configured else '未配置'}）\n"
                f"fetch: timeout={config.fetch.timeout}s, max={config.fetch.max_response_bytes} bytes, "
                f"chars={config.fetch.max_chars}, redirects={config.fetch.max_redirects}"), True
    if sub == "providers":
        lines = []
        for name in ("zhipu", "serpapi", "searxng"):
            data = config.providers.get(name, {})
            active = name == config.search.provider
            has_key = bool((config.search.api_key if active else None) or data.get("api_key"))
            has_url = bool((config.search.api_base if active else None) or data.get("api_base") or data.get("url"))
            lines.append(f"{name}: {'已配置' if has_url and (has_key or name == 'searxng') else '未配置'}")
        return "\n".join(lines), True
    if sub == "search":
        if len(parts) < 3 or not parts[2].strip():
            return "用法: /web search <query>", False
        service = getattr(agent, "web_search", None)
        if service is None:
            return "Web 搜索未启用或未配置 provider。", False
        ok, output = await service.search_tool({"query": parts[2].strip()})
        return output, ok
    if sub == "fetch":
        if len(parts) < 3 or not parts[2].strip():
            return "用法: /web fetch <url>", False
        service = getattr(agent, "web_fetch", None)
        if service is None:
            return "Web 抓取未启用。", False
        ok, output = await service.fetch_tool({"url": parts[2].strip()})
        return output, ok
    return "用法: /web status|providers|search <query>|fetch <url>", False


async def execute_skill_command(agent: Any, raw: str) -> tuple[str, bool]:
    """Shared read-only Skill management command semantics."""
    registry = getattr(agent, "skill_registry", None)
    if registry is None or not registry.config.enabled:
        return "Skill 能力未启用。", False
    parts = raw.split()
    sub = parts[1].lower() if len(parts) > 1 else "list"
    if sub == "list":
        registry.reload()
        return registry.format_list(), True
    if sub == "load":
        if len(parts) < 3:
            return "用法: /skill load <name> [reference ...]", False
        ok, output = registry.manual_load(parts[2], tuple(parts[3:]))
        return output, ok
    if sub in {"enable", "disable"}:
        if len(parts) < 3:
            return f"用法: /skill {sub} <name>", False
        enabled = sub == "enable"
        ok = registry.set_enabled(parts[2], enabled)
        action = "启用" if enabled else "禁用"
        return (f"Skill {parts[2]} 已{action}。" if ok else f"Skill {parts[2]} 不存在或无法修改。"), ok
    return "用法: /skill list|load <name> [reference ...]|enable <name>|disable <name>", False


async def execute_history_command(agent: Any, raw: str) -> tuple[str, bool]:
    """Show or explicitly clear local input history without exposing entries."""
    history = getattr(agent, "input_history", None)
    if history is None:
        return "输入历史未初始化。", False
    parts = raw.split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    if sub in {"status", ""}:
        return history.status(), True
    if sub == "clear":
        count = history.clear(persistent=True)
        return f"已清理输入历史（{count} 条）。", True
    return "用法: /history status|clear", False
