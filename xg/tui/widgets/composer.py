from __future__ import annotations

import asyncio

from textual.events import Key
from textual.widgets import Input

from xg.tui.widgets.command_suggestions import CommandSuggestions


class Composer(Input):
    """Single-line first version; Textual handles focus and history later."""

    BINDINGS = [("escape", "escape_input", "清除输入或取消当前操作")]

    def __init__(self) -> None:
        super().__init__(placeholder="输入任务或 /help …", id="composer")
        self.suggestions_enabled = True

    def set_suggestions_enabled(self, enabled: bool) -> None:
        self.suggestions_enabled = enabled

    def _suggestions(self) -> CommandSuggestions | None:
        try:
            return self.app.query_one("#command-suggestions", CommandSuggestions)
        except Exception:
            # Input can receive an event while the app is mounting or
            # unmounting. In that short window there is no suggestion widget.
            return None

    def on_input_changed(self, event: Input.Changed) -> None:
        """Keep filtering local and leave submission to the App."""
        if event.input is not self or not self.suggestions_enabled:
            return
        suggestions = self._suggestions()
        if suggestions is not None:
            suggestions.update_query(event.value)

    def on_key(self, event: Key) -> None:
        """Keep plan-review shortcuts working while the input stays enabled."""
        app = self.app
        suggestions = self._suggestions()
        if suggestions is not None and suggestions.is_open:
            if event.key == "up":
                event.prevent_default()
                event.stop()
                suggestions.move_selection(-1)
                return
            if event.key == "down":
                event.prevent_default()
                event.stop()
                suggestions.move_selection(1)
                return
            if event.key == "tab":
                command = suggestions.selected_command()
                if command is not None:
                    event.prevent_default()
                    event.stop()
                    app.complete_command_suggestion(command)
                return
            if event.key == "escape":
                event.prevent_default()
                event.stop()
                suggestions.close()
                return

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
