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
