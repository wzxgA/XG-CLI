from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from xg.tui.renderables import render_item
from xg.tui.state import TuiState
from xg.tui.widgets.collapsible_card import CollapsibleCard
from xg.tui.widgets.action_card import InlineApprovalCard, InlineConfirmationCard


class TranscriptView(VerticalScroll):
    def update_progress(self, item_id: str, item) -> bool:
        """Update one local progress card without rebuilding the transcript."""
        for widget in self.children:
            if getattr(widget, "transcript_item_id", "") == item_id:
                widget.update(render_item(item))
                return True
        return False

    def update_state(self, state: TuiState) -> None:
        # StateChanged can arrive while the App is still composing its
        # children. Defer the first render until this scroll view is attached.
        if not self.is_attached:
            self.call_after_refresh(lambda: self.update_state(state))
            return
        self.remove_children()
        widgets = []
        if not state.transcript:
            widgets.append(
                Static(
                    "输入任务开始与 Agent 对话\n\n试试：/help   /plan <任务>   /model",
                    classes="transcript-empty-state",
                )
            )
        else:
            for item in state.transcript:
                if item.collapsible and item.kind in ("thinking", "tool_call", "tool_result", "approval"):
                    widgets.append(CollapsibleCard(item))
                else:
                    widget = Static(render_item(item))
                    widget.transcript_item_id = item.id
                    widgets.append(widget)
        if state.pending_approval is not None:
            widgets.append(InlineApprovalCard(state.pending_approval))
        elif state.pending_confirmation is not None:
            widgets.append(InlineConfirmationCard(state.pending_confirmation))
        if widgets:
            self.mount(*widgets)
        self.call_after_refresh(self.scroll_end, animate=False)
