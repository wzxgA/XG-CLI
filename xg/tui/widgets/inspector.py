"""The read-only, multi-view Inspector shown beside the transcript."""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.widgets import ContentSwitcher, Static

from xg.tui.messages import InspectorViewSelected
from xg.tui.state import InspectorView, TuiState


def _number(value: int, *, unavailable: bool = False) -> str:
    return "-" if unavailable else f"{value:,}"


def _ratio(value: float, *, unavailable: bool = False) -> str:
    return "-" if unavailable else f"{value * 100:.1f}%"


def _clip(value: str, width: int = 18) -> str:
    value = " ".join(value.split())
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


class InspectorTab(Static):
    """A stable, keyboard and mouse accessible Inspector tab."""

    can_focus = True
    BINDINGS = [
        ("enter", "select", "选择视图"),
        ("space", "select", "选择视图"),
    ]

    def __init__(self, view: InspectorView, label: str, *, id: str) -> None:
        self.view = view
        self.full_label = label
        super().__init__(label, id=id, markup=False)
        self.tooltip = label

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(InspectorViewSelected(self.view))

    def action_select(self) -> None:
        self.post_message(InspectorViewSelected(self.view))


class InspectorPanel(Vertical):
    """Four read-only views backed exclusively by :class:`TuiState`."""

    VIEWS: tuple[InspectorView, ...] = ("session", "plan", "memory", "safety")
    LABELS = {
        "session": "Session",
        "plan": "Plan",
        "memory": "Memory",
        "safety": "Safety",
    }
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Kept for direct rendering tests and compatibility with callers that
        # previously treated InspectorPanel as a Static.
        self._rendered_text = ""

    def compose(self):
        with Horizontal(id="inspector-tabs"):
            for view in self.VIEWS:
                yield InspectorTab(
                    view,
                    self.LABELS[view],
                    id=f"inspector-tab-{view}",
                )
        with ContentSwitcher(initial="inspector-session", id="inspector-content"):
            for view in self.VIEWS:
                yield Static("", id=f"inspector-{view}", markup=False)

    def render(self):
        # ``InspectorPanel`` used to be a Static. Returning the last active
        # view keeps standalone render() consumers useful while mounted
        # children provide the actual on-screen layout.
        if self.is_mounted:
            return ""
        return self._rendered_text

    def update_state(self, state: TuiState) -> None:
        inspector = state.inspector
        view = inspector.active_view if inspector.active_view in self.VIEWS else "session"
        self._rendered_text = self._view_text(view, state)
        if not self.is_mounted:
            return

        for candidate in self.VIEWS:
            tab = self.query_one(f"#inspector-tab-{candidate}", InspectorTab)
            tab.remove_class("active")
            if candidate == view:
                tab.add_class("active")
            content = self.query_one(f"#inspector-{candidate}", Static)
            content.update(self._view_text(candidate, state))
        self.query_one("#inspector-content", ContentSwitcher).current = f"inspector-{view}"

    def _view_text(self, view: InspectorView, state: TuiState) -> str:
        if view == "plan":
            return self._plan_text(state)
        if view == "memory":
            return self._memory_text(state)
        if view == "safety":
            return self._safety_text(state)
        return self._session_text(state)

    def _session_text(self, state: TuiState) -> str:
        inspector = state.inspector
        usage = inspector.usage
        status = {
            "idle": "Idle",
            "running": "Working",
            "awaiting_approval": "Waiting approval",
            "awaiting_plan_review": "Plan review",
            "error": "Error",
        }.get(state.phase, state.phase)
        return (
            f"Session\nprovider  {inspector.provider}\nmodel     {inspector.model}\n"
            f"status    {status}\n\n"
            "Context\n"
            f"estimated input  {_number(usage.estimated_prompt_tokens, unavailable=usage.estimated_prompt_tokens <= 0)} token\n"
            f"model window     {_number(usage.context_window, unavailable=usage.context_window <= 0)} token\n"
            f"window usage     {_ratio(usage.window_ratio, unavailable=usage.context_window <= 0)}\n"
            f"input budget     {_number(usage.request_token_limit, unavailable=usage.request_token_limit <= 0)} token\n"
            f"budget usage     {_ratio(usage.budget_usage_ratio, unavailable=usage.request_token_limit <= 0)}\n"
            f"source           {usage.usage_source}\n\n"
            "Last request\n"
            f"prompt           {_number(usage.last_prompt_tokens, unavailable=usage.usage_source != 'provider')}\n"
            f"completion       {_number(usage.last_completion_tokens, unavailable=usage.usage_source != 'provider')}\n"
            f"total            {_number(usage.last_total_tokens, unavailable=usage.usage_source != 'provider')}\n\n"
            "Session usage\n"
            f"prompt           {_number(usage.session_prompt_tokens)}\n"
            f"completion       {_number(usage.session_completion_tokens)}\n"
            f"total            {_number(usage.session_total_tokens)}\n\n"
            "Compaction\n"
            f"count            {usage.compaction_count}\n"
            f"last             {_number(usage.last_compaction_before)} → {_number(usage.last_compaction_after)}"
        )

    def _plan_text(self, state: TuiState) -> str:
        plan = state.inspector.plan
        round_text = (
            f"第 {plan.current_round} 轮 / 共 {plan.total_rounds} 轮"
            if plan.current_round > 0 and plan.total_rounds > 0 else "-"
        )
        status_icons = {"done": "✓", "running": "●", "failed": "!", "pending": "○"}
        lines = [
            "Plan",
            f"status            {plan.status}",
            f"goal              {plan.goal or '-'}",
            f"current round     {round_text}",
            f"progress          {plan.completed_tasks} / {plan.total_tasks} tasks",
            f"failures          {plan.failure_count}",
            "",
            "Tasks",
        ]
        if plan.tasks:
            lines.extend(
                f"{status_icons.get(task.status, '·')} {task.id}  {_clip(task.title)}  {task.status}"
                for task in plan.tasks
            )
        else:
            lines.append("暂无计划")
        return "\n".join(lines)

    def _memory_text(self, state: TuiState) -> str:
        memory = state.inspector.memory
        available = "available" if memory.store_available else "unavailable"
        return (
            "Memory\n"
            f"project root      {memory.project_root or '-'}\n\n"
            "Project memory\n"
            f"XG.md             {'loaded' if memory.xg_loaded else 'not found'}\n"
            f"XG.local.md       {'loaded' if memory.xg_local_loaded else 'not found'}\n"
            f"warnings          {memory.warning_count}\n\n"
            "Long-term memory\n"
            f"entries           {memory.memory_count}\n"
            f"last operation    {memory.last_operation or '-'}\n"
            f"store             {available}"
        )

    def _safety_text(self, state: TuiState) -> str:
        safety = state.inspector.safety
        hitl = "on" if safety.hitl_enabled else "off"
        allow_all = "yes" if safety.session_allow_all else "no"
        return (
            "Safety\n"
            f"HITL              {hitl}\n"
            f"session allow all {allow_all}\n\n"
            "Current approval\n"
            f"status            {safety.approval_status}\n"
            f"tool              {safety.current_tool or '-'}\n"
            f"level             {safety.current_level or '-'}\n\n"
            "Last decision\n"
            f"decision          {safety.last_decision or '-'}\n"
            f"reason            {safety.last_reason or '-'}\n\n"
            "Policy\n"
            f"PathGuard         {'active' if safety.path_guard_active else 'inactive'}\n"
            f"CommandGuard      {'active' if safety.command_guard_active else 'inactive'}\n"
            f"last rejection    {safety.last_rejection or '-'}"
        )
