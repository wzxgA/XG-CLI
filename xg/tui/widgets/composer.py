from __future__ import annotations

from textual.widgets import Input


class Composer(Input):
    """Single-line first version; Textual handles focus and history later."""

    def __init__(self) -> None:
        super().__init__(placeholder="输入任务或 /help …", id="composer")
