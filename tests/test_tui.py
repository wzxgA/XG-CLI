from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from xg.agent.react import AgentEvent, ReActAgent
from xg.agent.plan import Plan, PlanTask
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.llm.client import LlmClient
from xg.llm.types import StreamEvent
from xg.tool.builtin import build_registry
from xg.tui.app import XgTuiApp
from xg.tui.controller import SessionController
from xg.tui.reducer import reduce_agent_event
from xg.tui.state import TuiState
from xg.tui.state import ApprovalRequest
from xg.tui.widgets.approval_modal import ApprovalModal
from xg.tui.widgets.plan_modal import PlanModal


class DummyClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="content", text="hello")
        yield StreamEvent(kind="done")


def make_context(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(DummyClient(), build_registry(base_dir=project), settings)
    return agent, settings, manager


def test_reducer_merges_streaming_content_and_ignores_stale_turn():
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(state, AgentEvent(kind="content", text="he"), "turn-1")
    state = reduce_agent_event(state, AgentEvent(kind="content", text="llo"), "turn-1")
    assert len(state.transcript) == 1
    assert state.transcript[0].text == "hello"
    stale = reduce_agent_event(state, AgentEvent(kind="content", text="bad"), "turn-old")
    assert stale.transcript[0].text == "hello"


@pytest.mark.asyncio
async def test_controller_submit_returns_to_idle(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    controller = SessionController(agent, settings, manager)
    await controller.submit("say hello")
    assert controller.state.phase == "idle"
    assert [item.kind for item in controller.state.transcript] == ["user", "assistant"]
    assert controller.state.transcript[-1].text == "hello"


@pytest.mark.asyncio
async def test_tui_pilot_layout_and_input(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        assert app.query_one("#transcript")
        assert app.query_one("#composer").has_focus
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.phase == "idle"


@pytest.mark.asyncio
async def test_tui_modals_open_and_close(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    plan = Plan("demo", [PlanTask("t1", "one", "do one", [])], [["t1"]])
    async with app.run_test(size=(120, 30)) as pilot:
        app.push_screen(PlanModal(plan))
        await pilot.pause()
        await pilot.press("escape")
        app.push_screen(ApprovalModal(ApprovalRequest("execute_command", "always", {"command": "echo hi"})))
        await pilot.pause()
        await pilot.press("r")
