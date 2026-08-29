"""Formatting helpers for the shared slash-command help."""

from __future__ import annotations

from collections.abc import Sequence

from xg.cli.commands import SLASH_COMMANDS, SlashCommandSpec


CATEGORY_LABELS: dict[str, str] = {
    "workflow": "工作流",
    "config": "配置与能力",
    "session": "会话",
    "memory": "记忆",
    "safety": "安全",
    "control": "控制",
    "general": "通用",
}

CATEGORY_ORDER = (
    "workflow",
    "config",
    "session",
    "memory",
    "safety",
    "control",
    "general",
)

SHORTCUTS = (
    ("Enter", "发送输入"),
    ("↑ / ↓", "浏览输入历史；输入 / 时浏览命令建议"),
    ("Ctrl+C", "取消当前任务"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+R", "显示或隐藏侧栏"),
    ("Esc", "关闭弹窗、取消当前交互或清除输入"),
)


def _command_key(value: str) -> str:
    """Normalize a command name or alias for exact help lookup."""

    return value.strip().lower().lstrip("/")


def parse_help_command(raw: str) -> str | None:
    """Return the optional help query, or ``None`` for a non-help input."""

    parts = raw.strip().split(maxsplit=1)
    if not parts or parts[0].lower() not in ("/help", "/?"):
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def _aliases_text(spec: SlashCommandSpec) -> str:
    if not spec.aliases:
        return ""
    return f"（别名：{'、'.join(spec.aliases)}）"


def _command_lines(commands: Sequence[SlashCommandSpec]) -> list[str]:
    visible = [spec for spec in commands if spec.usage and spec.description]
    if not visible:
        return []
    usage_width = max(len(spec.usage) for spec in visible)
    lines: list[str] = []
    for category in CATEGORY_ORDER:
        grouped = [spec for spec in visible if spec.category == category]
        if not grouped:
            continue
        lines.append(CATEGORY_LABELS.get(category, category))
        for spec in grouped:
            usage = spec.usage.ljust(usage_width)
            lines.append(f"  {usage}  {spec.description}{_aliases_text(spec)}")
        lines.append("")

    # Keep custom categories visible even if a future command adds one that
    # is not yet part of the standard presentation order.
    known = set(CATEGORY_ORDER)
    for category in dict.fromkeys(spec.category for spec in visible if spec.category not in known):
        lines.append(CATEGORY_LABELS.get(category, category))
        for spec in visible:
            if spec.category == category:
                usage = spec.usage.ljust(usage_width)
                lines.append(f"  {usage}  {spec.description}{_aliases_text(spec)}")
        lines.append("")
    return lines


def format_help(
    commands: Sequence[SlashCommandSpec] = SLASH_COMMANDS,
    *,
    include_shortcuts: bool = True,
) -> str:
    """Return the complete, renderer-neutral slash-command help text."""

    lines = ["XG 命令帮助", ""]
    lines.extend(_command_lines(commands))

    aliases = [
        f"{alias} = {spec.name}"
        for spec in commands
        for alias in spec.aliases
    ]
    if aliases:
        lines.extend(["命令别名", f"  {'    '.join(aliases)}", ""])

    if include_shortcuts:
        lines.append("TUI 快捷键")
        shortcut_width = max(len(key) for key, _ in SHORTCUTS)
        lines.extend(
            f"  {key.ljust(shortcut_width)}  {description}"
            for key, description in SHORTCUTS
        )
        lines.append("")

    lines.extend(
        [
            "提示：输入 / 后可以使用 ↑ / ↓ 选择命令建议。",
            "输入 /help <命令> 可查看某条命令的详细用法。",
        ]
    )
    return "\n".join(lines).rstrip()


def format_command_help(
    query: str,
    commands: Sequence[SlashCommandSpec] = SLASH_COMMANDS,
) -> str:
    """Return help for one command or an actionable unknown-command hint."""

    token = query.strip().split(maxsplit=1)[0] if query.strip() else ""
    normalized = _command_key(token)
    if not normalized:
        return format_help(commands)

    spec = find_command(token, commands)
    if spec is None:
        return f"未找到命令帮助：{token}。输入 /help 查看全部命令。"

    lines = [f"{spec.name} — {spec.description}", f"用法：{spec.usage}"]
    if spec.aliases:
        lines.append(f"别名：{'、'.join(spec.aliases)}")
    return "\n".join(lines)


def find_command(
    query: str,
    commands: Sequence[SlashCommandSpec] = SLASH_COMMANDS,
) -> SlashCommandSpec | None:
    """Find a command by its name or alias."""

    normalized = _command_key(query)
    if not normalized:
        return None
    return next(
        (
            item
            for item in commands
            if _command_key(item.name) == normalized
            or any(_command_key(alias) == normalized for alias in item.aliases)
        ),
        None,
    )
