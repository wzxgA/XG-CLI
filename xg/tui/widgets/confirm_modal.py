from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from xg.tui.state import ConfirmationRequest


class ConfirmModal(ModalScreen[str]):
    BINDINGS = [("escape", "cancel", "取消"), ("enter", "confirm", "确认"), ("y", "confirm", "确认")]

    def __init__(self, request: ConfirmationRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(f"{self.request.title}\n\n{self.request.body}")
            yield Button("确认 (y/Enter)", id="confirm", variant="warning")
            yield Button("取消 (Esc)", id="cancel")

    def action_confirm(self) -> None:
        self.dismiss("confirm")

    def action_cancel(self) -> None:
        self.dismiss("cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")
