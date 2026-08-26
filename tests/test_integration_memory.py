"""第五期 Agent/CLI 记忆接入测试。"""

from __future__ import annotations

from typing import AsyncIterator

from xg.agent.react import ReActAgent
from xg.cli.app import _handle_command
from xg.config.settings import Settings
from xg.llm.client import LlmClient
from xg.llm.types import Message, StreamEvent
from xg.memory.manager import MemoryManager
from xg.tool.builtin import build_registry


class ReplyClient(LlmClient):
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        self.requests.append(list(messages))
        yield StreamEvent(kind="content", text="已收到项目记忆")
        yield StreamEvent(kind="done")


async def test_react_injects_project_and_long_term_memory(tmp_path):
    (tmp_path / "XG.md").write_text("测试命令是 uv run pytest", encoding="utf-8")
    memory = MemoryManager(tmp_path)
    memory.save("API 字段必须使用 snake_case")
    client = ReplyClient()
    settings = Settings(api_base="https://test", api_key="k", model="m")
    agent = ReActAgent(
        llm=client,
        tools=build_registry(base_dir=tmp_path),
        settings=settings,
        memory_manager=memory,
    )

    events = [event async for event in agent.run("检查项目约定")]

    assert events[-1].kind == "done"
    request = client.requests[0]
    rendered = "\n".join(message.content for message in request)
    assert "uv run pytest" in rendered
    assert "snake_case" in rendered


def test_memory_commands_save_search_delete(tmp_path):
    memory = MemoryManager(tmp_path)
    agent = ReActAgent(
        llm=ReplyClient(),
        tools=build_registry(base_dir=tmp_path),
        settings=Settings(),
        memory_manager=memory,
    )

    saved, should_exit = _handle_command(agent, Settings(), None, "/save 使用 uv run pytest")
    assert not should_exit
    assert "#1" in saved
    listed, _ = _handle_command(agent, Settings(), None, "/memory search pytest")
    assert "uv run pytest" in listed
    deleted, _ = _handle_command(agent, Settings(), None, "/memory delete 1")
    assert "已删除" in deleted
