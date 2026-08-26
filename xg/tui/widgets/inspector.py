from __future__ import annotations

from textual.widgets import Static

from xg.tui.state import TuiState


class InspectorPanel(Static):
    def update_state(self, state: TuiState) -> None:
        s = state.inspector
        hitl = "on" if s.hitl_enabled else "off"
        self.update(
            f"Session\nprovider  {s.provider}\nmodel     {s.model}\n\n"
            f"Plan      {s.plan_status}\nBatch     {s.batch or '-'}\n"
            f"Memory    {s.memory_count}\nHITL      {hitl}"
        )
