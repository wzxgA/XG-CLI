"""Rich renderables for transcript items."""

from __future__ import annotations

import json

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from xg.tui.diagrams import FlowchartModel, FlowchartParseError, parse_flowchart, render_flowchart, split_mermaid_blocks
from xg.tui.plan_renderables import PlanReviewCard
from xg.tui.state import TranscriptItem


def _truncate(value: str, limit: int = 20_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n… (输出已截断)"


def _mermaid_source(source: str) -> str:
    return "```mermaid\n" + _truncate(source) + "\n```"


def _trace_status(item: TranscriptItem) -> str:
    return {
        "streaming": "进行中",
        "running": "执行中",
        "success": "成功",
        "failed": "失败",
        "cancelled": "已取消",
        "done": "已完成",
    }.get(item.status, item.status)


def _trace_summary(item: TranscriptItem) -> str:
    if item.kind == "thinking":
        label = "思考"
    elif item.kind == "tool_call":
        label = f"工具调用 · {item.tool_name}"
    elif item.kind == "tool_result":
        label = f"工具结果 · {item.tool_name}"
    else:
        label = item.kind
    source = item.text or item.tool_args or "无详细内容"
    first_line = next((line.strip() for line in source.splitlines() if line.strip()), "无详细内容")
    return f"{label} · {_trace_status(item)} · {first_line[:100]}"


def trace_renderable(item: TranscriptItem):
    """Render a collapsible trace item; the widget owns click behavior."""
    marker = "▶" if item.collapsed else "▼"
    if item.kind == "thinking":
        label = "思考"
        detail = _truncate(item.text, 12_000)
    elif item.kind == "tool_call":
        label = f"工具调用 · {item.tool_name}"
        try:
            detail = json.dumps(json.loads(item.tool_args), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            detail = item.tool_args
        detail = _truncate(detail, 4_000)
    elif item.kind == "tool_result":
        label = f"工具结果 · {item.tool_name}"
        detail = _truncate(item.text, 12_000)
    else:
        label = f"审批 · {item.tool_name}"
        detail = _truncate(item.text or "工具审批结果", 2_000)
    body = detail if not item.collapsed else _trace_summary(item)
    style = {
        "streaming": "yellow",
        "running": "yellow",
        "success": "green",
        "failed": "red",
        "cancelled": "yellow",
    }.get(item.status, "cyan")
    title = f"{marker} {label} · {_trace_status(item)}"
    return Panel(Text(body), title=title, border_style=style)


class DiagramCard:
    """Rich renderable for one Mermaid block inside the transcript."""

    def __init__(
        self,
        source: str,
        model: FlowchartModel | None,
        *,
        error: str = "",
        source_visible: bool = False,
    ) -> None:
        self.source = source
        self.model = model
        self.error = error
        self.source_visible = source_visible

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        available = max(1, options.max_width - 4)
        if self.model is None:
            body = f"Mermaid flowchart 暂无法渲染：{self.error}\n\n{_mermaid_source(self.source)}"
            title = "Flowchart · parse failed"
            yield Panel(Text(_truncate(body)), title=title, border_style="bright_blue")
            return
        else:
            result = render_flowchart(self.model, width=available)
            title = f"Flowchart · {self.model.direction} · {len(self.model.nodes)} nodes · {len(self.model.edges)} edges"
            if result.mode != "unicode":
                title += f" · {result.mode}"
            parts: list[object] = [Text(result.text, no_wrap=True, overflow="crop")]
            if result.warnings:
                parts.append(Text("\n\n⚠ " + "；".join(result.warnings)))
            if self.source_visible:
                parts.append(Text("\n\n" + _mermaid_source(self.source)))
            parts.append(Text("\n\n[d] 查看/隐藏 Mermaid 源码 · [r] 重新布局"))
            yield Panel(Group(*parts), title=title, border_style="bright_blue")
            return


def _assistant_renderable(item: TranscriptItem):
    parts: list[object] = []
    for text, block in split_mermaid_blocks(_truncate(item.text)):
        if block is None:
            if text:
                try:
                    parts.append(Markdown(text))
                except Exception:
                    parts.append(Text(text))
            continue
        try:
            model = parse_flowchart(block.source)
            parts.append(DiagramCard(block.source, model, source_visible=item.diagram_source_visible))
        except FlowchartParseError as exc:
            # An unsupported block remains visible as source; a malformed
            # diagram must never prevent the rest of the assistant message.
            parts.append(DiagramCard(block.source, None, error=str(exc), source_visible=True))
    if not parts:
        parts.append(Text("…"))
    return Panel(Group(*parts), title="XG", border_style="green" if not item.streaming else "yellow")


def render_item(item: TranscriptItem):
    if item.kind == "user":
        return Panel(Text(item.text), title="你", border_style="cyan")
    if item.kind == "progress":
        return Panel(
            Text(f"… {item.text}"),
            title="XG · Working",
            border_style="yellow",
        )
    if item.kind == "assistant":
        return _assistant_renderable(item)
    if item.kind in ("thinking", "tool_call", "tool_result", "approval") and item.collapsible:
        return trace_renderable(item)
    if item.kind == "tool_call":
        try:
            args = json.dumps(json.loads(item.tool_args), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            args = item.tool_args
        return Panel(Text(_truncate(args, 4_000)), title=f"工具调用 · {item.tool_name}", border_style="cyan")
    if item.kind == "tool_result":
        style = "green" if item.tool_ok else "red"
        return Panel(Text(_truncate(item.text)), title=f"工具结果 · {item.tool_name}", border_style=style)
    if item.kind == "approval":
        return Panel(Text(item.text or "工具审批结果"), title=f"审批 · {item.tool_name}", border_style="yellow")
    if item.kind == "context":
        return Panel(Text(item.text), title="上下文", border_style="blue")
    if item.kind == "help":
        return Panel(Text(_truncate(item.text)), title="命令帮助", border_style="cyan")
    if item.kind == "plan":
        plan = item.plan
        if plan is None:
            return Panel(Text(item.text), title="计划", border_style="magenta")
        if item.plan_review:
            return PlanReviewCard(item)
        lines = [f"目标：{plan.goal}", f"共 {len(plan.batches)} 轮"]
        for batch_no, batch in enumerate(plan.batches, 1):
            lines.append(f"第 {batch_no} 轮：{', '.join(batch)}")
            for task_id in batch:
                task = plan.task_by_id(task_id)
                if task is None:
                    continue
                lines.append(f"  [{task.status}] {task.id}  {task.title}")
                if not item.collapsed:
                    deps = f"（依赖：{', '.join(task.deps)}）" if task.deps else ""
                    lines.append(f"      {task.description}{deps}")
        lines.append("")
        lines.append("Enter 执行 · d 展开/折叠详情 · r 重规划 · Esc 取消")
        return Panel(Text("\n".join(lines)), title="计划审阅", border_style="magenta")
    if item.kind == "error":
        return Panel(Text(item.text), title="错误", border_style="red")
    return Text(item.text, style="dim")
