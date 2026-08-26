"""Rich renderables for transcript items."""

from __future__ import annotations

import json

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from xg.tui.state import TranscriptItem


def _truncate(value: str, limit: int = 20_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n… (输出已截断)"


def render_item(item: TranscriptItem):
    if item.kind == "user":
        return Panel(Text(item.text), title="你", border_style="cyan")
    if item.kind == "assistant":
        try:
            body = Markdown(_truncate(item.text) or "…")
        except Exception:
            body = Text(item.text)
        return Panel(body, title="XG", border_style="green" if not item.streaming else "yellow")
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
    if item.kind == "plan":
        plan = item.plan
        if plan is None:
            return Panel(Text(item.text), title="计划", border_style="magenta")
        lines = [f"目标：{plan.goal}", f"批次：{len(plan.batches)}"]
        for batch_no, batch in enumerate(plan.batches, 1):
            lines.append(f"批次 {batch_no}：{', '.join(batch)}")
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
