from __future__ import annotations

from textual.widgets import Static

from xg.tui.state import TuiState


def _number(value: int, *, unavailable: bool = False) -> str:
    return "-" if unavailable else f"{value:,}"


def _ratio(value: float, *, unavailable: bool = False) -> str:
    return "-" if unavailable else f"{value * 100:.1f}%"


class InspectorPanel(Static):
    def update_state(self, state: TuiState) -> None:
        s = state.inspector
        u = s.usage
        hitl = "on" if s.hitl_enabled else "off"
        self.update(
            f"Session\nprovider  {s.provider}\nmodel     {s.model}\n\n"
            f"Context\n"
            f"estimated input  {_number(u.estimated_prompt_tokens, unavailable=u.estimated_prompt_tokens <= 0)} token\n"
            f"model window     {_number(u.context_window, unavailable=u.context_window <= 0)} token\n"
            f"window usage     {_ratio(u.window_ratio, unavailable=u.context_window <= 0)}\n"
            f"input budget     {_number(u.request_token_limit, unavailable=u.request_token_limit <= 0)} token\n"
            f"budget usage     {_ratio(u.budget_usage_ratio, unavailable=u.request_token_limit <= 0)}\n"
            f"source           {u.usage_source}\n\n"
            f"Last request\n"
            f"prompt           {_number(u.last_prompt_tokens, unavailable=u.usage_source != 'provider')}\n"
            f"completion       {_number(u.last_completion_tokens, unavailable=u.usage_source != 'provider')}\n"
            f"total            {_number(u.last_total_tokens, unavailable=u.usage_source != 'provider')}\n\n"
            f"Session usage\n"
            f"prompt           {_number(u.session_prompt_tokens)}\n"
            f"completion       {_number(u.session_completion_tokens)}\n"
            f"total            {_number(u.session_total_tokens)}\n\n"
            f"Compaction\n"
            f"count            {u.compaction_count}\n"
            f"last             {_number(u.last_compaction_before)} → {_number(u.last_compaction_after)}\n\n"
            f"Plan      {s.plan_status}\nBatch     {s.batch or '-'}\n"
            f"Memory    {s.memory_count}\nHITL      {hitl}"
        )
