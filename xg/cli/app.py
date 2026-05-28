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
from xg.config.manager import ConfigManager, mask_key
from xg.config.settings import Settings, load_settings
from xg.llm.client import LlmClient, LlmError
from xg.llm.factory import create_client
from xg.tool.builtin import build_registry

console = Console()

BANNER = """\
[XG] Agent CLI v0.1.0
输入任务开始对话；/model 切换 provider 或模型，/config 查看/设置配置，/clear 清空上下文，/exit 退出。
"""


def build_agent(settings: Settings, base_dir=None) -> ReActAgent:
    client = create_client(settings.api_base, settings.api_key, settings.model)
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


async def run_loop(agent: ReActAgent, settings: Settings, manager: ConfigManager) -> None:
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
            message, should_exit = _handle_command(agent, settings, manager, user_input)
            if message:
                console.print(Text(message, style="dim"))
            if should_exit:
                return
            continue

        try:
            await handle_turn(agent, user_input)
        except KeyboardInterrupt:
            console.print(Text("（已中断本轮任务）", style="yellow"))
        except LlmError as e:
            console.print(Panel(Text(f"请求失败: {e}"), style="red"))


def _handle_command(
    agent: ReActAgent, settings: Settings, manager: ConfigManager, raw: str
) -> tuple[str | None, bool]:
    """处理斜杠命令。返回 (输出消息, 是否退出程序)。"""
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        return "再见。", True
    if cmd == "/clear":
        agent.clear()
        return "上下文已清空。", False
    if cmd == "/model":
        return _cmd_model(agent, settings, manager, arg), False
    if cmd == "/config":
        return _cmd_config(agent, settings, manager, arg), False
    return f"未知命令: {cmd}。可用: /model /config /clear /exit", False


def _cmd_model(
    agent: ReActAgent, settings: Settings, manager: ConfigManager, arg: str
) -> str:
    if not arg:
        active = manager.active()
        lines = [
            f"当前: {active.provider_name} / {active.model}（窗口 {active.context_window}）",
            "可用 providers:",
        ]
        for p in manager.list_providers():
            cache = "cache" if p.supports_cache else "-"
            vision = "vision" if p.supports_vision else "-"
            lines.append(
                f"  {p.name:<10} {p.default_model:<18} 窗口 {p.context_window:<6} {cache:<5} {vision}"
            )
        return "\n".join(lines)

    if "/" in arg:
        provider_name, model = (x.strip() for x in arg.split("/", 1))
        return _switch(agent, settings, manager, provider_name, model)
    if arg in manager.provider_names():
        return _switch(agent, settings, manager, arg, None)
    # 不带 provider 前缀时，视为当前 provider 内的模型切换
    return _switch(agent, settings, manager, settings.provider, arg)


def _switch(
    agent: ReActAgent,
    settings: Settings,
    manager: ConfigManager,
    provider_name: str,
    model: str | None,
) -> str:
    """切换到指定 provider（可选指定模型）。失败返回错误消息，不改变现状。"""
    provider = manager.resolve_provider(provider_name)
    if provider is None:
        return f"未知 provider: {provider_name}，可用: {', '.join(manager.provider_names())}"
    key = manager.resolve_api_key(provider)
    if not key:
        return (
            f"缺少 {provider.api_key_env} 配置，无法切换到 {provider.name}。"
            f"请在 .env / 环境变量中配置。"
        )
    model = model or provider.default_model

    api_base = manager.resolve_api_base(provider)
    settings.provider = provider.name
    settings.model = model
    settings.api_base = api_base
    settings.api_key = key
    settings.context_window = manager.resolve_window(provider)
    agent.llm = create_client(api_base, key, model)
    manager.set_active(provider.name, model)
    return f"已切换: {provider.display_name} / {model}"


def _cmd_config(
    agent: ReActAgent, settings: Settings, manager: ConfigManager, arg: str
) -> str:
    parts = arg.split()
    sub = parts[0].lower() if parts else ""

    if sub == "list":
        header = f"{'provider':<14}{'默认模型':<20}{'窗口':<8}cache  vision"
        lines = [header]
        for p in manager.list_providers():
            lines.append(
                f"{p.name:<14}{p.default_model:<20}{p.context_window:<8}"
                f"{'✓' if p.supports_cache else '-'}     "
                f"{'✓' if p.supports_vision else '-'}"
            )
        return "\n".join(lines)

    if sub == "get":
        if len(parts) < 2:
            return "用法: /config get <key>（如 active_provider / providers.deepseek.default_model）"
        value = manager.get_config_value(parts[1])
        return f"{parts[1]} = {value if value is not None else '(未设置)'}"

    if sub == "set":
        if len(parts) < 3:
            return "用法: /config set <key> <value>"
        key, value = parts[1], parts[2]
        if key == "active_provider":
            return _switch(agent, settings, manager, value, None)
        if key == "active_model":
            return _switch(agent, settings, manager, settings.provider, value)
        manager.set_config_value(key, value)
        return f"已设置 {key} = {value}（持久化到 {manager.user_config_path}）"

    active = manager.active()
    return "\n".join(
        [
            f"provider: {active.provider_name}",
            f"model:    {active.model}",
            f"api_base: {active.api_base}",
            f"api_key:  {mask_key(active.api_key)}",
            f"窗口:     {active.context_window} token",
            f"cache:    {'✓' if active.supports_cache else '-'}    vision: {'✓' if active.supports_vision else '-'}",
        ]
    )


def main() -> None:
    manager = ConfigManager()
    settings = load_settings(manager)
    if not settings.api_base or not settings.api_key:
        console.print(
            Panel(
                Text(
                    "缺少 API Key 配置。请在 .env / 环境变量中配置，例如 "
                    "XG_OPENAI_API_KEY 或 XG_DEEPSEEK_API_KEY；或用 /model 切换 provider。"
                ),
                style="red",
            )
        )
        sys.exit(1)
    if not settings.model:
        console.print(Panel(Text("缺少模型配置（XG_MODEL 或 provider 默认模型）。"), style="red"))
        sys.exit(1)

    agent = build_agent(settings)
    try:
        asyncio.run(run_loop(agent, settings, manager))
    except KeyboardInterrupt:
        console.print("再见。")


if __name__ == "__main__":
    main()
