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
        return Panel(Text(item.text), title="计划待审阅", border_style="magenta")
    if item.kind == "error":
        return Panel(Text(item.text), title="错误", border_style="red")
    return Text(item.text, style="dim")
