from __future__ import annotations

from rich.console import Console

from xg.agent.plan import Plan, PlanTask
from xg.tui.plan_renderables import PlanReviewCard, plan_to_flowchart
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
    assert "批次流程" in collapsed
    assert "t1 准备基础" in collapsed
    assert "t4 汇总结果" in collapsed
    assert "读取配置" in collapsed
    assert "完整的基础准备细节" not in collapsed

    expanded = render_card(TranscriptItem(
        id="plan-1", kind="plan", plan=plan, plan_review=True, collapsed=False
    ))
    assert "完整的基础准备细节" in expanded
    assert "依赖：t1" in expanded


def test_plan_review_view_switches_off_when_plan_is_approved() -> None:
    plan = make_plan()
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_plan_event(state, PlanEvent(kind="plan_generated", plan=plan), "turn-1")
    assert state.transcript[-1].plan_review is True
    state = reduce_plan_event(state, PlanEvent(kind="approved", plan=plan), "turn-1")
    assert state.transcript[-1].plan_review is False
