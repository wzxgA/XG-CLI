from __future__ import annotations

import asyncio

from textual.events import Key
from textual.widgets import Input


class Composer(Input):
    """Single-line first version; Textual handles focus and history later."""

    BINDINGS = [("escape", "escape_input", "清除输入或取消当前操作")]

    def __init__(self) -> None:
        super().__init__(placeholder="输入任务或 /help …", id="composer")

    def on_key(self, event: Key) -> None:
        """Keep plan-review shortcuts working while the input stays enabled."""
        app = self.app
        has_pending_plan = getattr(app, "_has_pending_plan", lambda: False)()
        if not has_pending_plan or getattr(app, "_replan_mode", False):
            if (
                event.key == "d"
                and not self.value
                and getattr(app, "_has_detail_toggle_target", lambda: False)()
            ):
                event.prevent_default()
                event.stop()
                app.action_plan_details()
            return
        if event.key == "r":
            event.prevent_default()
            event.stop()
            app.action_plan_replan()
        elif event.key == "d":
            event.prevent_default()
            event.stop()
            app.action_plan_details()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            app.action_plan_execute()
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            asyncio.create_task(app.action_escape())

    async def action_escape_input(self) -> None:
        # Input consumes Escape before it bubbles to the App. Delegate to the
        # app so idle, running, plan-review and replan states share one policy.
        await self.app.action_escape()
