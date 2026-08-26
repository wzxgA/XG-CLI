from __future__ import annotations

from textual.widgets import Static

from xg.tui.state import TuiState


class QueueStatus(Static):
    """Compact preview of submissions waiting behind the active turn."""

    MAX_PREVIEW_ITEMS = 4
    PREVIEW_LENGTH = 72

    def update_state(self, state: TuiState) -> None:
        if not state.queue:
            self.update("")
            self.display = False
            return

        lines = [f"队列 {len(state.queue)} 项"]
        for item in state.queue[: self.MAX_PREVIEW_ITEMS]:
            preview = " ".join(item.text.split())
            if len(preview) > self.PREVIEW_LENGTH:
                preview = preview[: self.PREVIEW_LENGTH - 1] + "…"
            lines.append(f"  #{item.id.removeprefix('queue-')} {preview}")
        remaining = len(state.queue) - self.MAX_PREVIEW_ITEMS
        if remaining > 0:
            lines.append(f"  还有 {remaining} 项")
        self.update("\n".join(lines))
        self.display = True
