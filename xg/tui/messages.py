"""Small Textual messages used to bridge controller callbacks to widgets."""

from __future__ import annotations

from textual.message import Message

from xg.tui.state import TuiState


class StateChanged(Message):
    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self.state = state


class TraceCardToggled(Message):
    """A user clicked or activated one collapsible execution card."""

    def __init__(self, item_id: str) -> None:
        super().__init__()
        self.item_id = item_id


class CommandSuggestionSelected(Message):
    """A local command suggestion was selected without being submitted."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
