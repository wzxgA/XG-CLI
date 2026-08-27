"""Inline action cards used for all TUI confirmations.

Cards are read-only text: context, risk and the input protocol.  Decisions
are always typed into the main Composer, so no Button or inner Input lives
here.
"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from xg.tui.state import ApprovalRequest, ConfirmationRequest

APPROVAL_HINT = "输入 y 批准 · a 全部放行 · r 拒绝 · s 跳过 · e 修改参数"
CONFIRMATION_HINTS = {
    "init": "输入 y 确认写入 · n 取消",
    "memory_clear": "输入 clear 确认清空 · 其他输入取消",
}


class InlineConfirmationCard(Vertical):
    def __init__(self, request: ConfirmationRequest) -> None:
        # Per-kind class lets the theme give each confirmation its own
        # border color while sharing one simple layout.  No fixed widget id:
        # TranscriptView rebuilds children on every state change and Textual
        # removes old nodes asynchronously, so duplicate ids would collide.
        super().__init__(
            classes=f"inline-action-card inline-confirmation-card confirmation-{request.kind}",
        )
        self.request = request
        # The Static is created eagerly: compose() runs asynchronously, so
        # updates must not depend on the card being mounted already.
        hint = CONFIRMATION_HINTS.get(self.request.kind, "输入 y 确认 · n 取消")
        self._text = Static(f"{self.request.title}\n\n{self.request.body}\n\n{hint}")

    def compose(self) -> ComposeResult:
        yield self._text


class InlineApprovalCard(Vertical):
    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__(classes="inline-action-card inline-approval-card")
        self.request = request
        self._mode = ""
        self._modified_args: dict | None = None
        self._text = Static(self._render_text(), classes="inline-approval-text")

    def compose(self) -> ComposeResult:
        yield self._text

    def set_mode(self, mode: str = "", modified_args: dict | None = None) -> None:
        """Switch the card text; empty mode means waiting for the decision."""
        self._mode = mode
        self._modified_args = modified_args
        self._text.update(self._render_text())

    def _render_text(self) -> str:
        if self._mode == "approval_edit":
            return (
                f"需要人工审批：{self.request.tool_name}\n\n"
                "请输入修改后的完整 JSON 参数。\n"
                "修改后的参数仍会经过 PathGuard / CommandGuard 检查。\n"
                "输入 Esc 取消修改。"
            )
        if self._mode == "approval_confirm_modified":
            args = json.dumps(self._modified_args or {}, ensure_ascii=False, indent=2)
            return (
                f"需要人工审批：{self.request.tool_name}\n\n"
                f"参数已修改为：\n{args}\n\n"
                "输入 y 确认执行 · r 拒绝执行 · Esc 取消"
            )
        args = json.dumps(self.request.args, ensure_ascii=False, indent=2)
        return (
            "需要人工审批\n"
            f"工具：{self.request.tool_name}\n"
            f"敏感级别：{self.request.level}\n"
            f"当前参数：\n{args}\n\n"
            f"{APPROVAL_HINT}"
        )
