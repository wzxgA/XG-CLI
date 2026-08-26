"""Interactive execution-trace card for the fullscreen TUI."""

from __future__ import annotations

from textual.events import Click
from textual.widgets import Static

from xg.tui.messages import TraceCardToggled
from xg.tui.renderables import trace_renderable
from xg.tui.state import TranscriptItem


class CollapsibleCard(Static):
    """State-driven card whose header/body can be toggled by mouse or key."""

    can_focus = True
    BINDINGS = [
        ("enter", "toggle", "展开/折叠"),
        ("space", "toggle", "展开/折叠"),
    ]

    def __init__(self, item: TranscriptItem) -> None:
        self.item_id = item.id
        self.item = item
        super().__init__(trace_renderable(item))

    def update_item(self, item: TranscriptItem) -> None:
        self.item = item
        self.update(trace_renderable(item))

    def _toggle(self) -> None:
        self.post_message(TraceCardToggled(self.item_id))

    def on_click(self, event: Click) -> None:
        event.stop()
        self._toggle()

    def action_toggle(self) -> None:
        self._toggle()

