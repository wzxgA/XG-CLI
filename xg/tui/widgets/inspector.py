"""The read-only, multi-view Inspector shown beside the transcript."""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.widgets import ContentSwitcher, Static
from rich.text import Text

from xg.tui.messages import InspectorViewSelected
from xg.tui.i18n import UiLanguage, normalize_language, translate, translate_status
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


INSPECTOR_STYLES = {
    "title": "bold bright_cyan",
    "section": "bold cyan",
    "label": "dim",
    "value": "white",
    "metric": "bright_cyan",
    "info": "blue",
    "running": "yellow",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "empty": "dim",
}

STATUS_STYLES = {
    "idle": ("○", "empty"),
    "running": ("●", "running"),
    "working": ("●", "running"),
    "waiting": ("◐", "warning"),
    "waiting approval": ("◐", "warning"),
    "review": ("◐", "info"),
    "plan review": ("◐", "info"),
    "done": ("✓", "success"),
    "success": ("✓", "success"),
    "approved": ("✓", "success"),
    "active": ("✓", "success"),
    "on": ("✓", "success"),
    "loaded": ("✓", "success"),
    "available": ("✓", "success"),
    "no": ("✓", "success"),
    "failed": ("!", "error"),
    "error": ("!", "error"),
    "rejected": ("!", "error"),
    "denied": ("!", "error"),
    "off": ("!", "warning"),
    "yes": ("!", "warning"),
    "cancelled": ("×", "warning"),
    "inactive": ("!", "warning"),
    "unavailable": ("×", "error"),
    "not found": ("-", "empty"),
}


def _status_style(status: str, *, fallback: str = "value") -> tuple[str, str]:
    return STATUS_STYLES.get(status.lower(), ("·", fallback))


def _metric_style(ratio: float, *, available: bool) -> str:
    if not available:
        return INSPECTOR_STYLES["empty"]
    if ratio >= 0.9:
        return INSPECTOR_STYLES["error"]
    if ratio >= 0.7:
        return INSPECTOR_STYLES["warning"]
    return INSPECTOR_STYLES["metric"]


def _bar_parts(done: int, total: int, width: int = 10) -> tuple[str, str] | None:
    if total <= 0:
        return None
    filled = min(width, max(0, round(done / total * width)))
    return "█" * filled, "░" * (width - filled)


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
    def __init__(self, *args, language: UiLanguage = "en", **kwargs) -> None:
        self.language = normalize_language(language)
        self._last_state: TuiState | None = None
        super().__init__(*args, **kwargs)
        # Kept for direct rendering tests and compatibility with callers that
        # previously treated InspectorPanel as a Static.
        self._rendered_text: Text | str = ""

    def compose(self):
        with Horizontal(id="inspector-tabs"):
            for view in self.VIEWS:
                yield InspectorTab(
                    view,
                    translate(self.language, f"view.{view}"),
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
        self._last_state = state
        self.language = normalize_language(state.ui_language)
        if self.is_mounted:
            self._update_tab_labels()
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

    def set_language(self, language: UiLanguage) -> None:
        """Change display language and immediately redraw the last snapshot."""
        self.language = normalize_language(language)
        if self.is_mounted:
            self._update_tab_labels()
        if self._last_state is not None:
            view = self._last_state.inspector.active_view
            self._rendered_text = self._view_text(view, self._last_state)
            if self.is_mounted:
                for candidate in self.VIEWS:
                    content = self.query_one(f"#inspector-{candidate}", Static)
                    content.update(self._view_text(candidate, self._last_state))
                self.query_one("#inspector-content", ContentSwitcher).current = f"inspector-{view}"

    def _update_tab_labels(self) -> None:
        for view in self.VIEWS:
            tab = self.query_one(f"#inspector-tab-{view}", InspectorTab)
            label = translate(self.language, f"view.{view}")
            tab.update(label)
            tab.full_label = label
            tab.tooltip = label

    def _view_text(self, view: InspectorView, state: TuiState) -> Text:
        if view == "plan":
            return self._plan_text(state)
        if view == "memory":
            return self._memory_text(state)
        if view == "safety":
            return self._safety_text(state)
        return self._session_text(state)

    def _t(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    @staticmethod
    def _title(text: Text, value: str) -> None:
        text.append(value + "\n", style=INSPECTOR_STYLES["title"])

    @staticmethod
    def _section(text: Text, value: str) -> None:
        text.append(value + "\n", style=INSPECTOR_STYLES["section"])

    @staticmethod
    def _field(text: Text, label: str, value: str, *, style: str = "value") -> None:
        text.append(f"{label:<17}", style=INSPECTOR_STYLES["label"])
        text.append(value + "\n", style=INSPECTOR_STYLES.get(style, style))

    def _status(self, text: Text, label: str, status: str, *, fallback: str = "value") -> None:
        icon, style = _status_style(status, fallback=fallback)
        text.append(f"{label:<17}", style=INSPECTOR_STYLES["label"])
        text.append(
            f"{icon} {translate_status(self.language, status)}\n",
            style=INSPECTOR_STYLES.get(style, style),
        )

    @staticmethod
    def _progress_field(
        text: Text,
        label: str,
        done: int,
        total: int,
        suffix: str,
        *,
        ratio: float | None = None,
    ) -> None:
        parts = _bar_parts(done, total)
        text.append(f"{label:<17}", style=INSPECTOR_STYLES["label"])
        if parts is None:
            text.append("-\n", style=INSPECTOR_STYLES["empty"])
            return
        ratio = ratio if ratio is not None else done / total
        style = _metric_style(ratio, available=True)
        text.append(parts[0], style=style)
        text.append(parts[1], style=INSPECTOR_STYLES["empty"])
        text.append(f" {suffix}\n", style=style)

    def _session_text(self, state: TuiState) -> Text:
        inspector = state.inspector
        usage = inspector.usage
        status = {
            "idle": "Idle",
            "running": "Working",
            "awaiting_approval": "Waiting approval",
            "awaiting_plan_review": "Plan review",
            "error": "Error",
        }.get(state.phase, state.phase)
        text = Text()
        self._title(text, self._t("view.session"))
        self._field(text, self._t("label.provider"), inspector.provider or "-", style="value" if inspector.provider else "empty")
        self._field(text, self._t("label.model"), inspector.model or "-", style="value" if inspector.model else "empty")
        self._status(text, self._t("label.status"), status, fallback="value")
        text.append("\n")
        self._section(text, self._t("section.context"))
        self._field(text, self._t("label.estimated_input"), f"{_number(usage.estimated_prompt_tokens, unavailable=usage.estimated_prompt_tokens <= 0)} {self._t('unit.token')}", style="metric")
        self._field(text, self._t("label.model_window"), f"{_number(usage.context_window, unavailable=usage.context_window <= 0)} {self._t('unit.token')}", style="metric")
        window_available = usage.context_window > 0
        self._progress_field(
            text, self._t("label.window_usage"), usage.estimated_prompt_tokens, usage.context_window,
            _ratio(usage.window_ratio, unavailable=not window_available), ratio=usage.window_ratio,
        )
        self._field(text, self._t("label.input_budget"), f"{_number(usage.request_token_limit, unavailable=usage.request_token_limit <= 0)} {self._t('unit.token')}", style="metric")
        budget_available = usage.request_token_limit > 0
        self._progress_field(
            text, self._t("label.budget_usage"), usage.estimated_prompt_tokens, usage.request_token_limit,
            _ratio(usage.budget_usage_ratio, unavailable=not budget_available), ratio=usage.budget_usage_ratio,
        )
        self._field(
            text,
            self._t("label.source"),
            translate_status(self.language, usage.usage_source),
            style="info" if usage.usage_source != "unavailable" else "empty",
        )
        text.append("\n")
        self._section(text, self._t("section.last_request"))
        provider_usage = usage.usage_source == "provider"
        self._field(text, self._t("label.prompt"), _number(usage.last_prompt_tokens, unavailable=not provider_usage), style="metric")
        self._field(text, self._t("label.completion"), _number(usage.last_completion_tokens, unavailable=not provider_usage), style="metric")
        self._field(text, self._t("label.total"), _number(usage.last_total_tokens, unavailable=not provider_usage), style="metric")
        text.append("\n")
        self._section(text, self._t("section.session_usage"))
        self._field(text, self._t("label.prompt"), _number(usage.session_prompt_tokens), style="metric")
        self._field(text, self._t("label.completion"), _number(usage.session_completion_tokens), style="metric")
        self._field(text, self._t("label.total"), _number(usage.session_total_tokens), style="metric")
        text.append("\n")
        self._section(text, self._t("section.compaction"))
        self._field(text, self._t("label.count"), str(usage.compaction_count), style="metric")
        self._field(text, self._t("label.last"), f"{_number(usage.last_compaction_before)} → {_number(usage.last_compaction_after)}", style="metric")
        return text

    def _plan_text(self, state: TuiState) -> Text:
        plan = state.inspector.plan
        round_text = (
            self._t("unit.round", current=plan.current_round, total=plan.total_rounds)
            if plan.current_round > 0 and plan.total_rounds > 0 else "-"
        )
        text = Text()
        self._title(text, self._t("view.plan"))
        self._status(text, self._t("label.status"), plan.status, fallback="value")
        self._field(text, self._t("label.goal"), plan.goal or "-", style="value" if plan.goal else "empty")
        self._field(text, self._t("label.current_round"), round_text, style="metric" if round_text != "-" else "empty")
        self._progress_field(text, self._t("label.progress"), plan.completed_tasks, plan.total_tasks, f"{plan.completed_tasks} / {plan.total_tasks} {self._t('unit.tasks')}")
        failures_style = "error" if plan.failure_count > 0 else "success"
        self._field(text, self._t("label.failures"), str(plan.failure_count), style=failures_style)
        text.append("\n")
        self._section(text, self._t("section.tasks"))
        if plan.tasks:
            for task in plan.tasks:
                icon, style = _status_style(task.status)
                text.append(f"{icon} ", style=INSPECTOR_STYLES.get(style, style))
                text.append(f"{task.id:<5}", style=INSPECTOR_STYLES["metric"])
                text.append(f"{_clip(task.title):<18}", style=INSPECTOR_STYLES["value"])
                text.append(
                    translate_status(self.language, task.status) + "\n",
                    style=INSPECTOR_STYLES.get(style, style),
                )
        else:
            text.append(self._t("empty.no_plan") + "\n", style=INSPECTOR_STYLES["empty"])
        return text

    def _memory_text(self, state: TuiState) -> Text:
        memory = state.inspector.memory
        text = Text()
        self._title(text, self._t("view.memory"))
        self._field(text, self._t("label.project_root"), memory.project_root or "-", style="value" if memory.project_root else "empty")
        text.append("\n")
        self._section(text, self._t("section.project_memory"))
        self._status(text, "XG.md", "loaded" if memory.xg_loaded else "not found", fallback="empty")
        self._status(text, "XG.local.md", "loaded" if memory.xg_local_loaded else "not found", fallback="empty")
        warning_style = "warning" if memory.warning_count > 0 else "success"
        self._field(text, self._t("label.warnings"), str(memory.warning_count), style=warning_style)
        text.append("\n")
        self._section(text, self._t("section.long_term_memory"))
        self._field(text, self._t("label.entries"), str(memory.memory_count), style="metric")
        self._field(text, self._t("label.last_operation"), memory.last_operation or "-", style="info" if memory.last_operation else "empty")
        self._status(text, self._t("label.store"), "available" if memory.store_available else "unavailable", fallback="value")
        return text

    def _safety_text(self, state: TuiState) -> Text:
        safety = state.inspector.safety
        text = Text()
        self._title(text, self._t("view.safety"))
        self._status(text, "HITL", "on" if safety.hitl_enabled else "off", fallback="value")
        self._status(text, self._t("label.session_allow_all"), "yes" if safety.session_allow_all else "no", fallback="value")
        text.append("\n")
        self._section(text, self._t("section.current_approval"))
        self._status(text, self._t("label.status"), safety.approval_status, fallback="value")
        self._field(text, self._t("label.tool"), safety.current_tool or "-", style="value" if safety.current_tool else "empty")
        self._field(text, self._t("label.level"), safety.current_level or "-", style="value" if safety.current_level else "empty")
        text.append("\n")
        self._section(text, self._t("section.last_decision"))
        if safety.last_decision:
            self._status(text, self._t("label.decision"), safety.last_decision, fallback="value")
        else:
            self._field(text, self._t("label.decision"), "-", style="empty")
        self._field(text, self._t("label.reason"), safety.last_reason or "-", style="value" if safety.last_reason else "empty")
        text.append("\n")
        self._section(text, self._t("section.policy"))
        self._status(text, "PathGuard", "active" if safety.path_guard_active else "inactive", fallback="value")
        self._status(text, "CommandGuard", "active" if safety.command_guard_active else "inactive", fallback="value")
        self._field(text, self._t("label.last_rejection"), safety.last_rejection or "-", style="error" if safety.last_rejection else "empty")
        return text
