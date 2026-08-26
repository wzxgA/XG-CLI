from __future__ import annotations

from textual.widgets import RichLog

from xg.tui.renderables import render_item
from xg.tui.state import TuiState


class TranscriptView(RichLog):
    def update_state(self, state: TuiState) -> None:
        self.clear()
        for item in state.transcript:
            self.write(render_item(item))
        self.scroll_end(animate=False)
