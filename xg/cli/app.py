"""CLI 交互循环：prompt_toolkit 输入 + rich 渲染 + 斜杠命令。"""

from __future__ import annotations

import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from xg.agent.react import AgentEvent, ReActAgent
from xg.config.settings import Settings
from xg.llm.client import LlmClient, LlmError
from xg.llm.openai_compat import OpenAICompatClient
from xg.tool.builtin import build_registry

console = Console()

BANNER = """\
[XG] Agent CLI v0.1.0
输入任务开始对话；/model <name> 切换模型，/clear 清空上下文，/exit 退出。
"""


def build_agent(settings: Settings, base_dir=None) -> ReActAgent:
    client: LlmClient = OpenAICompatClient(
        api_base=settings.api_base,
        api_key=settings.api_key,
        model=settings.model,
    )
    tools = build_registry(base_dir=base_dir, max_output_chars=settings.max_tool_output_chars)
    return ReActAgent(llm=client, tools=tools, settings=settings)


async def handle_turn(agent: ReActAgent, user_input: str) -> None:
    """执行一轮 ReAct 循环并渲染事件流。"""
    buffer = Text()
    with Live(console=console, vertical_overflow="visible", refresh_per_second=10) as live:
        live.update(Text(""))
        async for event in agent.run(user_input):
            if event.kind == "content":
                buffer.append(event.text)
                live.update(Markdown(buffer.plain))
            elif event.kind == "tool_call" and event.tool_call:
                live.update(Text(""))
                console.print(
                    Text(f"→ {event.tool_call.name}({event.tool_call.arguments})", style="dim cyan")
                )
                live.update(Text(""))
            elif event.kind == "tool_result" and event.tool_result:
                live.update(Text(""))
                style = "green" if event.tool_result.ok else "red"
                preview = (event.tool_result.output or event.tool_result.error).strip()
                if len(preview) > 300:
                    preview = preview[:300] + " ..."
                console.print(Text(f"  {'OK' if event.tool_result.ok else 'FAIL'}: {preview}", style=style))
                live.update(Text(""))
            elif event.kind in ("step_limit", "budget_exceeded", "error"):
                live.update(Text(""))
                if event.kind == "step_limit":
                    msg = "已达到单轮工具调用步数上限，循环终止。可继续输入让模型接着完成。"
                elif event.kind == "budget_exceeded":
                    msg = "上下文 token 已接近窗口上限，循环终止。可用 /clear 清空对话后继续。"
                else:
                    msg = f"请求失败: {event.text}"
                console.print(Panel(Text(msg), style="yellow"))
                return
    console.print(Text(""))


async def run_loop(agent: ReActAgent, settings: Settings) -> None:
    session: PromptSession[str] = PromptSession()
    console.print(Panel(BANNER, title="XG", border_style="cyan"))

    while True:
        try:
            user_input = await session.prompt_async(HTML("<ansicyan>xg ></ansicyan> "))
        except (KeyboardInterrupt, EOFError):
            console.print("再见。")
            return

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            if _handle_command(agent, settings, user_input):
                return
            continue

        try:
            await handle_turn(agent, user_input)
        except KeyboardInterrupt:
            console.print(Text("（已中断本轮任务）", style="yellow"))
        except LlmError as e:
            console.print(Panel(Text(f"请求失败: {e}"), style="red"))


def _handle_command(agent: ReActAgent, settings: Settings, raw: str) -> bool:
    """处理斜杠命令。返回 True 表示退出程序。"""
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        console.print("再见。")
        return True
    if cmd == "/clear":
        agent.clear()
        console.print(Text("上下文已清空。", style="dim"))
        return False
    if cmd == "/model":
        if not arg:
            console.print(Text(f"当前模型: {settings.model}。用法: /model <name>", style="dim"))
            return False
        settings.model = arg
        # OpenAICompatClient 的 model 是实例属性，运行时替换
        agent.llm.model = arg  # type: ignore[attr-defined]
        console.print(Text(f"已切换模型: {arg}", style="dim"))
        return False
    console.print(Text(f"未知命令: {cmd}。可用: /model /clear /exit", style="yellow"))
    return False


def main() -> None:
    from xg.config.settings import load_settings

    settings = load_settings()
    if not settings.api_base or not settings.api_key:
        console.print(
            Panel(
                Text("缺少 API 配置。请复制 .env.example 为 .env 并填写 XG_API_BASE / XG_API_KEY。"),
                style="red",
            )
        )
        sys.exit(1)
    if not settings.model:
        console.print(Panel(Text("缺少 XG_MODEL 配置，请在 .env 中指定模型名。"), style="red"))
        sys.exit(1)

    agent = build_agent(settings)
    try:
        asyncio.run(run_loop(agent, settings))
    except KeyboardInterrupt:
        console.print("再见。")


if __name__ == "__main__":
    main()
