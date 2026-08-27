"""Inline action cards used for all TUI confirmations."""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Static

from xg.tui.state import ApprovalRequest, ConfirmationRequest


class InlineConfirmationCard(Vertical):
    can_focus = True
    BINDINGS = [
        ("enter", "confirm", "确认"),
        ("y", "confirm", "确认"),
        ("escape", "cancel", "取消"),
    ]

    def __init__(self, request: ConfirmationRequest) -> None:
        super().__init__(id="inline-confirmation-card", classes="inline-action-card")
        self.request = request

    def compose(self) -> ComposeResult:
        yield Static(f"{self.request.title}\n\n{self.request.body}")
        yield Button("确认 (y/Enter)", id="inline-confirm", variant="warning")
        yield Button("取消 (Esc)", id="inline-cancel")

    def action_confirm(self) -> None:
        self.app.handle_inline_confirmation(True)

    def action_cancel(self) -> None:
        self.app.handle_inline_confirmation(False)


class InlineApprovalCard(Vertical):
    can_focus = True
    BINDINGS = [
        ("enter", "approve", "批准"),
        ("y", "approve", "批准"),
        ("a", "allow_all", "全部放行"),
        ("r", "reject", "拒绝"),
        ("s", "skip", "跳过"),
        ("escape", "reject", "拒绝"),
        ("e", "edit", "修改参数"),
    ]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__(id="inline-approval-card", classes="inline-action-card")
        self.request = request

    def compose(self) -> ComposeResult:
        args = json.dumps(self.request.args, ensure_ascii=False, indent=2)
        yield Static(
            f"需要审批：{self.request.tool_name}\n"
            f"敏感级别：{self.request.level}\n\n{args}"
        )
        yield Input(placeholder="按 e 修改 JSON 参数", id="inline-approval-json")
        yield Button("批准 (Enter/y)", id="inline-approve", variant="success")
        yield Button("本会话全部放行 (a)", id="inline-allow-all")
        yield Button("拒绝 (r/Esc)", id="inline-reject", variant="error")
        yield Button("跳过 (s)", id="inline-skip")

    def action_approve(self) -> None:
        self.app.handle_inline_approval("approve")

    def action_allow_all(self) -> None:
        self.app.handle_inline_approval("allow_all")

    def action_reject(self) -> None:
        self.app.handle_inline_approval("reject")

    def action_skip(self) -> None:
        self.app.handle_inline_approval("skip")

    def action_edit(self) -> None:
        self.query_one("#inline-approval-json", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "inline-approval-json":
            return
        try:
            json.loads(event.value)
        except json.JSONDecodeError:
            event.input.value = ""
            return
        self.app.handle_inline_approval("modify:" + event.value)
