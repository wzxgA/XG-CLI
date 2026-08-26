"""Small Textual messages used to bridge controller callbacks to widgets."""

from __future__ import annotations

from textual.message import Message

from xg.tui.state import TuiState


class StateChanged(Message):
    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self.state = state
