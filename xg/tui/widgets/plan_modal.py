from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from xg.agent.plan import Plan, ReviewDecision


class PlanModal(ModalScreen[str]):
    BINDINGS = [("escape", "cancel", "取消"), ("enter", "execute", "执行"), ("d", "details", "详情"), ("r", "replan", "重新规划")]

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan
        self._details = False

    def compose(self) -> ComposeResult:
        yield from self._content()

    def _content(self) -> list:
        lines = [f"目标：{self.plan.goal}", f"任务：{len(self.plan.tasks)} 个，批次：{len(self.plan.batches)}"]
        for task in self.plan.tasks:
            lines.append(f"{task.id} [{task.status}] {task.title}")
            if self._details:
                lines.append(f"  {task.description}")
        return [Vertical(Static("\n".join(lines)), Input(placeholder="按 r 输入重新规划要求", id="plan-feedback"), Button("执行 (Enter)", id="execute", variant="success"), Button("取消 (Esc)", id="cancel", variant="error"))]

    def action_details(self) -> None:
        self._details = not self._details
        self.refresh(recompose=True)

    def action_cancel(self) -> None:
        self.dismiss("cancel")

    def action_execute(self) -> None:
        self.dismiss("execute")

    def action_replan(self) -> None:
        self.query_one("#plan-feedback", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "plan-feedback" and event.value.strip():
            self.dismiss("replan:" + event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")
