from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from xg.tui.renderables import render_item
from xg.tui.state import TuiState
from xg.tui.widgets.collapsible_card import CollapsibleCard


class TranscriptView(VerticalScroll):
    def update_state(self, state: TuiState) -> None:
        self.remove_children()
        widgets = []
        for item in state.transcript:
            if item.collapsible and item.kind in ("thinking", "tool_call", "tool_result", "approval"):
                widgets.append(CollapsibleCard(item))
            else:
                widgets.append(Static(render_item(item)))
        if widgets:
            self.mount(*widgets)
        self.call_after_refresh(self.scroll_end, animate=False)
