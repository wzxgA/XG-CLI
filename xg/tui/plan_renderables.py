"""Plan review flowchart renderables for the Textual transcript."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.panel import Panel
from rich.text import Text

from xg.agent.plan import Plan
from xg.tui.diagrams.model import FlowchartEdge, FlowchartModel, FlowchartNode
from xg.tui.diagrams.renderer import render_flowchart
from xg.tui.state import TranscriptItem


def _brief(value: str, limit: int = 100) -> str:
    """Return a local, deterministic one-line task summary."""
    first = next((line.strip() for line in value.splitlines() if line.strip()), "暂无概括")
    first = " ".join(first.split())
    if cell_len(first) <= limit:
        return first
    out = ""
    used = 0
    for char in first:
        size = cell_len(char)
        if used + size > max(1, limit - 1):
            break
        out += char
        used += size
    return out + "…"


@dataclass(frozen=True)
class PlanFlowchartData:
    model: FlowchartModel
    rank_by_node: dict[str, int]
    ordered_ids: tuple[str, ...]


def plan_to_flowchart(plan: Plan) -> PlanFlowchartData:
    """Adapt an already validated Plan DAG to the text flowchart model."""
    tasks = {task.id: task for task in plan.tasks}
    ordered_ids: list[str] = []
    rank_by_node: dict[str, int] = {}
    for batch_no, batch in enumerate(plan.batches):
        for task_id in batch:
            if task_id in tasks and task_id not in ordered_ids:
                ordered_ids.append(task_id)
                rank_by_node[task_id] = batch_no
    # Defensive fallback for a manually-created Plan whose batches are stale.
    for task in plan.tasks:
        if task.id not in ordered_ids:
            ordered_ids.append(task.id)
            rank_by_node[task.id] = len(plan.batches)

    nodes = [
        FlowchartNode(id=task_id, label=f"{task_id} {tasks[task_id].title}")
        for task_id in ordered_ids
    ]
    known = set(tasks)
    edges = [
        FlowchartEdge(source=dependency, target=task.id)
        for task in plan.tasks
        for dependency in task.deps
        if dependency in known and task.id in known
    ]
    warnings = []
    missing = [
        f"{task.id} 依赖不存在的任务 {dependency}"
        for task in plan.tasks
        for dependency in task.deps
        if dependency not in known
    ]
    warnings.extend(missing)
    return PlanFlowchartData(
        model=FlowchartModel(direction="TD", nodes=nodes, edges=edges, warnings=warnings),
        rank_by_node=rank_by_node,
        ordered_ids=tuple(ordered_ids),
    )


def _task_summary_lines(plan: Plan, ordered_ids: tuple[str, ...], detailed: bool) -> list[str]:
    tasks = {task.id: task for task in plan.tasks}
    lines = ["任务详情" if detailed else "任务概括"]
    for task_id in ordered_ids:
        task = tasks.get(task_id)
        if task is None:
            continue
        if not detailed:
            lines.append(f"{task.id} {task.title} · {_brief(task.description)}")
            continue
        deps = ", ".join(task.deps) if task.deps else "无"
        lines.extend([
            f"{task.id} {task.title}",
            f"  描述：{task.description or '暂无描述'}",
            f"  依赖：{deps}",
        ])
    return lines


class PlanReviewCard:
    """Static review card: flowchart plus summary/details below it."""

    def __init__(self, item: TranscriptItem) -> None:
        self.item = item

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        plan = self.item.plan
        if not isinstance(plan, Plan):
            yield Panel(Text(self.item.text or "计划不可用"), title="计划审阅", border_style="magenta")
            return
        data = plan_to_flowchart(plan)
        chart = render_flowchart(
            data.model,
            width=max(1, options.max_width - 8),
            rank_by_node=data.rank_by_node,
        )
        chart_parts: list[object] = [Text(chart.text, no_wrap=True, overflow="crop")]
        if chart.warnings:
            chart_parts.append(Text("\n\n⚠ " + "；".join(chart.warnings)))
        parts: list[object] = [
            Text(f"目标：{plan.goal}\n批次：{len(plan.batches)}", style="bold"),
            Panel(Group(*chart_parts), title="批次流程", border_style="cyan"),
        ]
        batch_lines = [
            f"批次 {batch_no}: {', '.join(batch)}"
            for batch_no, batch in enumerate(plan.batches, 1)
        ]
        parts.append(Text("\n".join(batch_lines), style="dim"))
        parts.append(Text("\n".join(_task_summary_lines(plan, data.ordered_ids, not self.item.collapsed))))
        parts.append(Text("\nEnter 执行 · d 查看详细描述 · r 重规划 · Esc 取消", style="dim"))
        yield Panel(Group(*parts), title="计划审阅", border_style="magenta")
