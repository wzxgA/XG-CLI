from __future__ import annotations

from textual.widgets import Static

from xg.tui.state import TuiState


class HeaderBar(Static):
    def update_state(self, state: TuiState) -> None:
        status = {
            "idle": "Idle", "running": "Working", "awaiting_approval": "Waiting approval",
            "awaiting_plan_review": "Plan review", "error": "Error",
        }.get(state.phase, state.phase)
        inspector = state.inspector
        self.update(f"XG  ·  {inspector.provider}/{inspector.model}  ·  {status}")
