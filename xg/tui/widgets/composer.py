from __future__ import annotations

from textual.widgets import Input


class Composer(Input):
    """Single-line first version; Textual handles focus and history later."""

    BINDINGS = [("escape", "escape_input", "清除输入或取消当前操作")]

    def __init__(self) -> None:
        super().__init__(placeholder="输入任务或 /help …", id="composer")

    async def action_escape_input(self) -> None:
        # Input consumes Escape before it bubbles to the App. Delegate to the
        # app so idle, running, plan-review and replan states share one policy.
        await self.app.action_escape()
