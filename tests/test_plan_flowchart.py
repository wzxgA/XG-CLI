from __future__ import annotations

from rich.console import Console
from rich.text import Text

from xg.agent.plan import Plan, PlanTask
from xg.tui.plan_renderables import PlanReviewCard, _task_summary_renderable, plan_to_flowchart
from xg.agent.plan import PlanEvent
from xg.tui.reducer import reduce_plan_event
from xg.tui.state import TuiState, TranscriptItem


def make_plan() -> Plan:
    tasks = [
        PlanTask("t1", "准备基础", "读取配置\n完整的基础准备细节", []),
        PlanTask("t2", "准备前端", "检查前端目录", ["t1"]),
        PlanTask("t3", "准备后端", "检查后端目录", ["t1"]),
        PlanTask("t4", "汇总结果", "合并前端和后端检查结果", ["t2", "t3"]),
    ]
    return Plan(goal="检查项目", tasks=tasks, batches=[["t1"], ["t2", "t3"], ["t4"]])


def test_plan_to_flowchart_preserves_branch_and_merge_dependencies() -> None:
    data = plan_to_flowchart(make_plan())
    assert data.rank_by_node == {"t1": 0, "t2": 1, "t3": 1, "t4": 2}
    assert [(node.id, node.label) for node in data.model.nodes] == [
        ("t1", "t1 准备基础"),
        ("t2", "t2 准备前端"),
        ("t3", "t3 准备后端"),
        ("t4", "t4 汇总结果"),
    ]
    assert [(edge.source, edge.target) for edge in data.model.edges] == [
        ("t1", "t2"), ("t1", "t3"), ("t2", "t4"), ("t3", "t4")
    ]
    assert all("完整的基础准备细节" not in node.label for node in data.model.nodes)


def render_card(item: TranscriptItem) -> str:
    console = Console(width=120, record=True)
    console.print(PlanReviewCard(item))
    return console.export_text()


def test_plan_review_card_shows_summary_by_default_and_details_after_d() -> None:
    plan = make_plan()
    collapsed = render_card(TranscriptItem(id="plan-1", kind="plan", plan=plan, plan_review=True))
    assert "轮次流程" in collapsed
    assert "共 3 轮" in collapsed
    assert "第 1 轮：t1" in collapsed
    assert "第 2 轮：t2, t3" in collapsed
    assert "按 d 显示详情" in collapsed
    assert "t1 准备基础" in collapsed
    assert "t4 汇总结果" in collapsed
    assert "读取配置" not in collapsed
    assert "完整的基础准备细节" not in collapsed

    expanded = render_card(TranscriptItem(
        id="plan-1", kind="plan", plan=plan, plan_review=True, collapsed=False
    ))
    assert "完整的基础准备细节" in expanded
    assert "依赖：t1" in expanded
    assert "按 d 收起详情" in expanded


def test_plan_review_view_switches_off_when_plan_is_approved() -> None:
    plan = make_plan()
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_plan_event(state, PlanEvent(kind="plan_generated", plan=plan), "turn-1")
    assert state.transcript[-1].plan_review is True
    state = reduce_plan_event(state, PlanEvent(kind="approved", plan=plan), "turn-1")
    assert state.transcript[-1].plan_review is False


def test_plan_review_card_omits_flow_panel_when_structured_fallback() -> None:
    """When the flowchart is too wide and falls back to structured text,
    hide the cyan 「轮次流程」panel. The downgrade warning is still shown
    dimmed inside the main card, and batch/task summaries remain intact."""

    plan = make_plan()
    narrow_console = Console(width=20, record=True)
    narrow_console.print(PlanReviewCard(
        TranscriptItem(id="plan-1", kind="plan", plan=plan, plan_review=True)
    ))
    rendered = narrow_console.export_text()
    assert "轮次流程" not in rendered
    # The long warning is wrapped across lines on a 20-column console,
    # so assert key fragments independently (each must appear in some line
    # when surrounding panel padding is stripped).
    stripped_lines = [line.strip() for line in rendered.splitlines()]
    assert any("图表超出当前终端" in line for line in stripped_lines)
    assert any("结构化文本" in line for line in stripped_lines)
    assert "共 3 轮" in rendered
    assert "第 1 轮：t1" in rendered
    assert "t1 准备基础" in rendered

    # Wide console should keep the 「轮次流程」 panel as before.
    wide_console = Console(width=160, record=True)
    wide_console.print(PlanReviewCard(
        TranscriptItem(id="plan-1", kind="plan", plan=plan, plan_review=True)
    ))
    assert "轮次流程" in wide_console.export_text()


def test_plan_detail_hint_is_dim_without_dimming_task_content() -> None:
    summary = _task_summary_renderable(make_plan(), ("t1",), detailed=False)
    details = _task_summary_renderable(make_plan(), ("t1",), detailed=True)

    assert isinstance(summary.renderables[1], Text)
    assert summary.renderables[1].plain == "按 d 显示详情"
    assert summary.renderables[1].style == "dim"
    assert isinstance(summary.renderables[2], Text)
    assert summary.renderables[2].plain == "t1 准备基础"
    assert summary.renderables[2].style == "bold"
    assert isinstance(details.renderables[1], Text)
    assert details.renderables[1].plain == "按 d 收起详情"
    assert details.renderables[1].style == "dim"
    assert isinstance(details.renderables[3], Text)
    assert details.renderables[3].style == "dim"
