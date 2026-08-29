from __future__ import annotations

from xg.agent.react import AgentEvent
from xg.llm.types import Usage
from xg.agent.plan import Plan, PlanEvent, PlanTask
from xg.tui.reducer import reduce_agent_event, reduce_plan_event
from xg.tui.state import (
    InspectorState,
    PlanInspectorSnapshot,
    PlanTaskSnapshot,
    SafetyInspectorSnapshot,
    TuiState,
)
from xg.tui.widgets.header import HeaderBar
from xg.tui.widgets.inspector import InspectorPanel


def test_reducer_tracks_context_ratios_and_provider_session_usage() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(
            kind="context_usage",
            estimated_prompt_tokens=8_000,
            request_token_limit=80_000,
            context_window=100_000,
        ),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.window_ratio == 0.08
    assert usage.budget_usage_ratio == 0.1
    assert state.inspector.context_tokens == 8_000
    assert state.inspector.context_window == 100_000

    state = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(100, 20, 120)),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.last_total_tokens == 120
    assert usage.session_prompt_tokens == 100
    assert usage.session_completion_tokens == 20
    assert usage.session_total_tokens == 120
    assert usage.usage_source == "provider"


def test_reducer_ignores_invalid_and_stale_usage() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(10, 2, 12)),
        "turn-1",
    )
    stale = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(100, 100, 200)),
        "turn-old",
    )
    assert stale.inspector.usage.session_total_tokens == 12

    invalid = reduce_agent_event(
        state,
        AgentEvent(kind="usage", usage=Usage(-1, 2, 1)),
        "turn-1",
    )
    assert invalid.inspector.usage.session_total_tokens == 12


def test_context_compaction_is_structured() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(
            kind="context_usage",
            estimated_prompt_tokens=7_900,
            request_token_limit=102_400,
            context_window=128_000,
            compaction_before=18_240,
            compaction_after=7_900,
        ),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.compaction_count == 1
    assert (usage.last_compaction_before, usage.last_compaction_after) == (18_240, 7_900)


def test_usage_widgets_show_unavailable_until_data_arrives() -> None:
    state = TuiState()
    header = HeaderBar()
    inspector = InspectorPanel()
    header.update_state(state)
    inspector.update_state(state)
    assert "Context -/-" in str(header.render())
    assert "source           unavailable" in str(inspector.render())


def test_inspector_uses_semantic_styles_for_session_values_and_status() -> None:
    state = TuiState(
        phase="running",
        inspector=InspectorState(
            provider="deepseek",
            model="deepseek-chat",
        ),
    )
    inspector = InspectorPanel()
    inspector.update_state(state)
    rendered = inspector.render()
    assert rendered.plain.startswith("Session\n")
    assert any(span.style == "bold bright_cyan" for span in rendered.spans)
    assert any(span.style == "yellow" for span in rendered.spans)
    assert "● Working" in rendered.plain


def test_inspector_plan_progress_and_task_statuses_are_colored() -> None:
    state = TuiState(
        inspector=InspectorState(
            active_view="plan",
            plan=PlanInspectorSnapshot(
                goal="检查项目",
                status="failed",
                completed_tasks=1,
                total_tasks=2,
                failure_count=1,
                tasks=(
                    PlanTaskSnapshot("t1", "读取配置", "done"),
                    PlanTaskSnapshot("t2", "执行测试", "failed"),
                ),
            ),
        )
    )
    inspector = InspectorPanel()
    inspector.update_state(state)
    rendered = inspector.render()
    assert "█████░░░░░ 1 / 2 tasks" in rendered.plain
    assert "✓ t1" in rendered.plain
    assert "! t2" in rendered.plain
    assert any(span.style == "green" for span in rendered.spans)
    assert any(span.style == "red" for span in rendered.spans)


def test_inspector_memory_and_safety_highlight_risks() -> None:
    state = TuiState(
        inspector=InspectorState(
            active_view="safety",
            safety=SafetyInspectorSnapshot(
                hitl_enabled=False,
                session_allow_all=True,
                approval_status="rejected",
                last_rejection="blocked command",
            ),
        )
    )
    inspector = InspectorPanel()
    inspector.update_state(state)
    rendered = inspector.render()
    assert "! off" in rendered.plain
    assert "! rejected" in rendered.plain
    assert "blocked command" in rendered.plain
    assert any(span.style == "red" for span in rendered.spans)


def test_header_contains_watermelon_brand_and_runtime_summary() -> None:
    state = TuiState(
        phase="running",
        inspector=InspectorState(
            provider="deepseek",
            model="deepseek-chat",
        ),
    )
    header = HeaderBar()
    header.update_state(state)
    rendered = str(header.render())
    assert "XG" in rendered
    assert "deepseek/deepseek-chat" in rendered
    assert "Working" in rendered
    assert "HITL ON" in rendered


def test_plan_reducer_populates_inspector_task_snapshot() -> None:
    plan = Plan(
        goal="检查项目",
        tasks=[
            PlanTask("t1", "读取配置", "读取配置文件", []),
            PlanTask("t2", "汇总结果", "整理结果", ["t1"]),
        ],
        batches=[["t1"], ["t2"]],
    )
    state = reduce_plan_event(
        TuiState(active_turn_id="turn-1", phase="running"),
        PlanEvent(kind="plan_generated", plan=plan),
        "turn-1",
    )
    assert state.inspector.plan.goal == "检查项目"
    assert state.inspector.plan.total_rounds == 2
    assert state.inspector.plan.tasks == (
        PlanTaskSnapshot("t1", "读取配置", "pending"),
        PlanTaskSnapshot("t2", "汇总结果", "pending"),
    )


def test_inspector_views_render_separately() -> None:
    state = TuiState(
        inspector=InspectorState(
            provider="test",
            model="model",
            active_view="safety",
        )
    )
    inspector = InspectorPanel()
    inspector.update_state(state)
    assert "Safety" in str(inspector.render())
    assert "HITL" in str(inspector.render())

    state.inspector.active_view = "memory"
    inspector.update_state(state)
    assert "Memory" in str(inspector.render())
    assert "Long-term memory" in str(inspector.render())
