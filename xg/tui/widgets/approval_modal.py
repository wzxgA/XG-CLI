from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from xg.safety.hitl import ApprovalDecision
from xg.tui.state import ApprovalRequest


class ApprovalModal(ModalScreen[str]):
    BINDINGS = [("escape", "reject", "拒绝"), ("enter", "approve", "批准"), ("y", "approve", "批准"), ("a", "allow_all", "全部放行"), ("r", "reject", "拒绝"), ("s", "skip", "跳过"), ("e", "edit", "修改参数")]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        args = json.dumps(self.request.args, ensure_ascii=False, indent=2)
        with Vertical(id="approval-dialog"):
            yield Static(f"需要审批：{self.request.tool_name}\n敏感级别：{self.request.level}\n\n{args}")
            yield Input(placeholder="按 e 修改 JSON 参数", id="approval-json")
            yield Button("批准 (Enter/y)", id="approve", variant="success")
            yield Button("本会话全部放行 (a)", id="allow-all")
            yield Button("拒绝 (r/Esc)", id="reject", variant="error")

    def _close(self, value: str) -> None:
        self.dismiss(value)

    def action_approve(self) -> None:
        self._close("approve")

    def action_allow_all(self) -> None:
        self._close("allow_all")

    def action_reject(self) -> None:
        self._close("reject")

    def action_skip(self) -> None:
        self._close("skip")

    def action_edit(self) -> None:
        self.query_one("#approval-json", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "approval-json":
            return
        try:
            json.loads(event.value)
        except json.JSONDecodeError:
            event.input.value = ""
            return
        self._close("modify:" + event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._close(event.button.id or "reject")
