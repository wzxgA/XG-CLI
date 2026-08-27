from __future__ import annotations

from textual.widgets import Static

from xg.tui.state import TuiState


def _compact(value: int) -> str:
    if value <= 0:
        return "-"
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{value / 1_000_000:.1f}m".replace(".0m", "m")


class HeaderBar(Static):
    def update_state(self, state: TuiState) -> None:
        status = {
            "idle": "Idle", "running": "Working", "awaiting_approval": "Waiting approval",
            "awaiting_plan_review": "Plan review", "error": "Error",
        }.get(state.phase, state.phase)
        inspector = state.inspector
        usage = inspector.usage
        level = "usage-normal"
        highest_ratio = max(usage.window_ratio, usage.budget_usage_ratio)
        if highest_ratio > 1.0:
            level = "usage-error"
        elif highest_ratio >= 0.7:
            level = "usage-warning"
        for class_name in ("usage-normal", "usage-warning", "usage-error"):
            self.remove_class(class_name)
        self.add_class(level)
        available = usage.estimated_prompt_tokens > 0 and usage.context_window > 0
        context = (
            f"{_compact(usage.estimated_prompt_tokens)}/{_compact(usage.context_window)}"
            if available else "-/-"
        )
        percentage = f"{usage.window_ratio * 100:.1f}%" if available else "-"
        self.update(
            f"XG  ·  {inspector.provider}/{inspector.model}  ·  {status}"
            f"  ·  Context {context}  ·  {percentage}"
        )
