"""Collapsible conversation group for one Team AgentRun."""

from __future__ import annotations

from textual.events import Click
from textual.widgets import Static

from xg.tui.messages import AgentGroupToggled
from xg.tui.renderables import agent_group_renderable
from xg.tui.state import AgentGroupState


class AgentGroupCard(Static):
    """One Agent conversation inside the shared TranscriptView."""

    can_focus = True
    BINDINGS = [
        ("enter", "toggle", "展开/折叠 Agent"),
        ("space", "toggle", "展开/折叠 Agent"),
    ]

    def __init__(self, group: AgentGroupState) -> None:
        self.group_id = group.group_id
        self.group = group
        super().__init__(agent_group_renderable(group))

    def update_group(self, group: AgentGroupState) -> None:
        self.group = group
        self.update(agent_group_renderable(group))

    def _toggle(self) -> None:
        self.post_message(AgentGroupToggled(self.group_id))

    def on_click(self, event: Click) -> None:
        event.stop()
        self._toggle()

    def action_toggle(self) -> None:
        self._toggle()
