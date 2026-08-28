"""Commands shared by the inline CLI and the fullscreen UI.

The command service deliberately returns data instead of printing it.  The
legacy helpers in :mod:`xg.cli.app` remain the compatibility implementation
for now; keeping this adapter small lets the TUI use the exact same command
semantics while the inline renderer is migrated incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
class SlashCommandSpec:
    """Read-only metadata shared by command help and TUI suggestions."""

    name: str
    aliases: tuple[str, ...] = ()
    usage: str = ""
    description: str = ""
    category: str = "general"


# Keep this tuple in presentation order.  It is intentionally metadata only;
# command execution remains in CommandService and the legacy compatibility
# helpers in xg.cli.app.
SLASH_COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec("/plan", usage="/plan <任务>", description="生成、审阅并执行计划", category="workflow"),
    SlashCommandSpec("/model", usage="/model [provider] [model]", description="查看或切换 provider / 模型", category="config"),
    SlashCommandSpec("/config", usage="/config get|set ...", description="查看或修改配置", category="config"),
    SlashCommandSpec("/mcp", usage="/mcp status|restart|logs|enable|disable|resources", description="管理 MCP Server", category="config"),
    SlashCommandSpec("/web", usage="/web status|providers|search|fetch", description="查看或使用只读联网能力", category="config"),
    SlashCommandSpec("/memory", usage="/memory list|search|delete|clear", description="管理长期记忆", category="memory"),
    SlashCommandSpec("/help", aliases=("/?",), usage="/help", description="查看命令帮助", category="general"),
    SlashCommandSpec("/init", usage="/init", description="初始化项目记忆", category="memory"),
    SlashCommandSpec("/save", usage="/save <内容>", description="保存长期记忆", category="memory"),
    SlashCommandSpec("/hitl", usage="/hitl on|off|reset", description="管理人工审批开关", category="safety"),
    SlashCommandSpec("/clear", usage="/clear", description="清空当前上下文", category="session"),
    SlashCommandSpec("/cancel", aliases=("/c",), usage="/cancel", description="取消当前任务", category="control"),
    SlashCommandSpec("/exit", aliases=("/quit",), usage="/exit", description="退出程序", category="control"),
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

        if raw.split(maxsplit=1)[0].lower() == "/mcp":
            message, ok = await execute_mcp_command(self.context.agent, raw)
            return CommandResult(ok=ok, message=message)
        if raw.split(maxsplit=1)[0].lower() == "/web":
            message, ok = await execute_web_command(self.context.agent, raw)
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
