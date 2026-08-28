from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from textual.widgets import Button, Input, Static

from xg.agent.react import AgentEvent, ReActAgent
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.llm.client import LlmClient
from xg.llm.types import StreamEvent, ToolCall, ToolResult
from xg.memory.manager import MemoryManager
from xg.safety.hitl import HITLPolicy
from xg.tool.builtin import build_registry
from xg.tui.app import XgTuiApp
from xg.tui.controller import SessionController
from xg.tui.reducer import reduce_agent_event
from xg.tui.state import TuiState
from xg.tui.state import ApprovalRequest
from xg.tui.widgets.approval_modal import ApprovalModal
from xg.tui.widgets.collapsible_card import CollapsibleCard


class DummyClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="content", text="hello")
        yield StreamEvent(kind="done")


class MermaidClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="content", text="流程如下：\n\n```mermaid\nflowchart LR\nA[开始] --> B[完成]\n```")
        yield StreamEvent(kind="done")


class TraceClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        if any(message.role == "tool" for message in messages):
            yield StreamEvent(kind="thinking", text="正在整理工具结果")
            yield StreamEvent(kind="content", text="检查完成")
        else:
            yield StreamEvent(kind="thinking", text="先读取项目目录")
            yield StreamEvent(kind="content", text="我先检查一下")
            yield StreamEvent(
                kind="tool_call",
                tool_call=ToolCall(id="call-list", name="list_dir", arguments="{}"),
            )
        yield StreamEvent(kind="done")


class PlanClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        if messages and "任务规划器" in messages[0].content:
            yield StreamEvent(
                kind="content",
                text='{"tasks":[{"id":"t1","title":"测试任务","description":"只观察","deps":[]}]}'
            )
        else:
            yield StreamEvent(kind="content", text="完成")
        yield StreamEvent(kind="done")


class SlowClient(LlmClient):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        self.started.set()
        await self.release.wait()
        yield StreamEvent(kind="content", text="finished")
        yield StreamEvent(kind="done")


class QueueClient(LlmClient):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        prompt = next(message.content for message in reversed(messages) if message.role == "user")
        self.calls.append(prompt)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
        yield StreamEvent(kind="content", text=f"done: {prompt}")
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


def test_reducer_reclassifies_intermediate_content_and_collapses_trace():
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(state, AgentEvent(kind="content", text="先检查"), "turn-1")
    state = reduce_agent_event(
        state,
        AgentEvent(kind="tool_call", tool_call=ToolCall("call-1", "list_dir", "{}")),
        "turn-1",
    )
    assert state.transcript[0].kind == "thinking"
    assert state.transcript[1].collapsible is True
    state = reduce_agent_event(
        state,
        AgentEvent(
            kind="tool_result",
            tool_result=ToolResult(
                tool_call_id="call-1", name="list_dir", ok=True, output="README.md"
            ),
        ),
        "turn-1",
    )
    assert state.transcript[0].collapsed is True
    assert state.transcript[1].collapsed is True
    assert state.transcript[2].collapsed is True
    state = reduce_agent_event(state, AgentEvent(kind="content", text="完成"), "turn-1")
    state = reduce_agent_event(state, AgentEvent(kind="done"), "turn-1")
    trace = [item for item in state.transcript if item.collapsible]
    assert trace and all(item.collapsed for item in trace)
    assert state.transcript[-1].kind == "assistant"
    assert state.transcript[-1].collapsed is False


@pytest.mark.asyncio
async def test_controller_submit_returns_to_idle(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    controller = SessionController(agent, settings, manager)
    await controller.submit("say hello")
    assert controller.state.phase == "idle"
    assert [item.kind for item in controller.state.transcript] == ["user", "assistant"]
    assert controller.state.transcript[-1].text == "hello"


@pytest.mark.asyncio
async def test_controller_queues_submissions_fifo(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    client = QueueClient()
    agent = ReActAgent(client, build_registry(base_dir=project), settings)
    controller = SessionController(agent, settings, manager)

    first = asyncio.create_task(controller.submit("first task"))
    await asyncio.wait_for(client.first_started.wait(), 1)
    second = asyncio.create_task(controller.submit("second task"))
    assert await second is True
    assert [item.text for item in controller.state.queue] == ["second task"]

    client.release_first.set()
    await asyncio.wait_for(first, 1)
    for _ in range(100):
        if client.calls == ["first task", "second task"] and not controller.busy:
            break
        await asyncio.sleep(0.01)

    assert client.calls == ["first task", "second task"]
    assert controller.state.queue == []
    assert [item.text for item in controller.state.transcript if item.kind == "user"] == [
        "first task", "second task"
    ]


@pytest.mark.asyncio
async def test_controller_cancel_current_turn_continues_queue(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    client = QueueClient()
    agent = ReActAgent(client, build_registry(base_dir=project), settings)
    controller = SessionController(agent, settings, manager)

    asyncio.create_task(controller.submit("first task"))
    await asyncio.wait_for(client.first_started.wait(), 1)
    assert await controller.submit("second task") is True
    assert len(controller.state.queue) == 1

    assert await controller.cancel() is True
    for _ in range(100):
        if client.calls == ["first task", "second task"] and not controller.busy:
            break
        await asyncio.sleep(0.01)

    assert client.calls == ["first task", "second task"]
    assert controller.state.queue == []


@pytest.mark.asyncio
async def test_tui_pilot_layout_and_input(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        assert app.query_one("#transcript")
        assert app.query_one("#composer-area")
        assert app.query_one("#composer-label").content == "输入"
        assert app.query_one("#composer").has_focus
        await pilot.press("h", "i")
        await pilot.press("escape")
        assert app.query_one("#composer").value == ""
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.phase == "idle"


@pytest.mark.asyncio
async def test_tui_uses_full_width_footer_composer_and_top_inspector(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)):
        shell = app.query_one("#shell")
        main = app.query_one("#main-column")
        inspector = app.query_one("#inspector")
        header = app.query_one("#header")
        transcript = app.query_one("#transcript")
        footer = app.query_one("#footer")
        composer_area = app.query_one("#composer-area")

        assert inspector.parent is shell
        assert header.parent is main
        assert transcript.parent is main
        # App-level children are mounted under Textual's default Screen.
        assert footer.parent is app.screen
        assert composer_area.parent is app.screen
        assert footer.region.y < composer_area.region.y
        assert inspector.region.y == shell.region.y
        assert inspector.region.height == shell.region.height
        assert header.region.height > transcript.region.height * 0.15


@pytest.mark.asyncio
async def test_tui_coalesces_state_renders_at_configured_rate(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    settings.tui_refresh_fps = 20
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        renders: list[str] = []
        original_render = app._render_state

        def track_render(state: TuiState) -> None:
            renders.append(state.notification)
            original_render(state)

        app._render_state = track_render
        app._on_state_change(TuiState(notification="first"))
        app._on_state_change(TuiState(notification="second"))
        app._on_state_change(TuiState(notification="latest"))
        await pilot.pause(0)

        assert renders == []
        await pilot.pause(0.1)
        assert renders == ["latest"]


@pytest.mark.asyncio
async def test_tui_empty_transcript_shows_brand_welcome_state(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        empty = app.query_one(".transcript-empty-state")
        assert "输入任务开始" in str(empty.render())
        assert "🍉" not in str(empty.render())
        assert "XG" not in str(empty.render())
        assert app.query_one("#composer").has_focus

        app.query_one("#composer").value = "hi"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert len(app.query(".transcript-empty-state")) == 0


@pytest.mark.asyncio
async def test_tui_inspector_views_switch_with_bindings_and_tabs(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        assert app.controller.state.inspector.active_view == "session"
        assert app.query_one("#inspector-tab-session")
        assert app.query_one("#inspector-tab-plan")
        assert app.query_one("#inspector-tab-memory")
        assert app.query_one("#inspector-tab-safety")
        assert app.query_one("#inspector-tab-session").render() == "Session"
        assert app.query_one("#inspector-tab-plan").render() == "Plan"

        await pilot.press("ctrl+2")
        await pilot.pause(0.1)
        assert app.controller.state.inspector.active_view == "plan"
        assert app.query_one("#inspector-content").current == "inspector-plan"

        await pilot.press("ctrl+tab")
        await pilot.pause()
        assert app.controller.state.inspector.active_view == "memory"

        await pilot.click(app.query_one("#inspector-tab-safety"))
        await pilot.pause()
        assert app.controller.state.inspector.active_view == "safety"


@pytest.mark.asyncio
async def test_tui_keeps_composer_available_and_shows_queue(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    client = QueueClient()
    agent = ReActAgent(client, build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#composer").value = "first task"
        await pilot.press("enter")
        await asyncio.wait_for(client.first_started.wait(), 1)

        app.query_one("#composer").value = "second task"
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert app.query_one("#composer").disabled is False
        queue_status = app.query_one("#queue-status")
        assert queue_status.display is True
        assert "second task" in str(queue_status.render())

        client.release_first.set()
        for _ in range(100):
            if client.calls == ["first task", "second task"] and not app.controller.busy:
                break
            await pilot.pause(0.01)
        await pilot.pause(0.1)
        assert client.calls == ["first task", "second task"]
        assert queue_status.display is False


@pytest.mark.asyncio
async def test_tui_command_suggestions_filter_complete_and_escape(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        suggestions = app.query_one("#command-suggestions")
        composer = app.query_one("#composer")
        assert suggestions.display is False

        composer.value = "/m"
        await pilot.pause()
        assert suggestions.display is True
        assert [spec.name for spec in suggestions.visible_specs] == ["/model", "/mcp", "/memory"]
        assert suggestions.highlighted == 0

        await pilot.press("down")
        assert suggestions.highlighted == 1
        await pilot.press("tab")
        await pilot.pause()
        assert composer.value == "/mcp"
        assert app.controller.state.transcript == []

        await pilot.press("escape")
        assert composer.value == "/mcp"
        assert suggestions.display is False


@pytest.mark.asyncio
async def test_tui_command_suggestion_mouse_selection_keeps_composer_focus(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        composer = app.query_one("#composer")
        suggestions = app.query_one("#command-suggestions")
        composer.value = "/m"
        await pilot.pause()

        assert await pilot.click(suggestions, offset=(4, 2))
        await pilot.pause()
        assert composer.value == "/mcp"
        assert composer.has_focus
        assert app.controller.state.transcript == []


@pytest.mark.asyncio
async def test_tui_command_suggestions_hidden_for_plan_review_and_replan(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(PlanClient(), build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        composer = app.query_one("#composer")
        suggestions = app.query_one("#command-suggestions")
        composer.value = "/plan 测试计划"
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.controller.state.phase == "awaiting_plan_review"
        assert suggestions.display is False

        await pilot.press("r")
        await pilot.pause()
        assert app._replan_mode is True
        composer.value = "/"
        await pilot.pause()
        assert suggestions.display is False


@pytest.mark.asyncio
async def test_tui_modals_open_and_close(tmp_path):
    agent, settings, manager = make_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.push_screen(ApprovalModal(ApprovalRequest("execute_command", "always", {"command": "echo hi"})))
        await pilot.pause()
        await pilot.press("r")


@pytest.mark.asyncio
async def test_tui_plan_renders_inline_without_opening_screen(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(PlanClient(), build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#composer").value = "/plan 测试计划"
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app.controller.state.phase == "awaiting_plan_review"
        assert app.controller.state.pending_plan is not None
        assert app.query_one("#composer").disabled is False
        assert app._modal_kind == ""
        assert len(app.screen_stack) == 1
        assert any(item.kind == "plan" for item in app.controller.state.transcript)
        await pilot.press("d")
        await pilot.pause()
        plan_item = next(item for item in app.controller.state.transcript if item.kind == "plan")
        assert plan_item.collapsed is False
        await pilot.press("r")
        await pilot.pause()
        assert app._replan_mode is True
        await pilot.press("escape")
        await pilot.pause()
        assert app.controller.state.phase == "idle"
        assert app._replan_mode is False


@pytest.mark.asyncio
async def test_tui_mermaid_renders_inline_and_d_toggles_source(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(MermaidClient(), build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#composer").value = "show diagram"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.controller.state.phase == "idle"
        item = next(item for item in app.controller.state.transcript if item.kind == "assistant")
        assert item.diagram_source_visible is False
        assert len(app.screen_stack) == 1
        await pilot.press("d")
        await pilot.pause()
        item = next(item for item in app.controller.state.transcript if item.kind == "assistant")
        assert item.diagram_source_visible is True


@pytest.mark.asyncio
async def test_tui_escape_cancels_running_task(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    client = SlowClient()
    agent = ReActAgent(client, build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#composer").value = "long task"
        await pilot.press("enter")
        await asyncio.wait_for(client.started.wait(), 1)
        assert app.controller.busy is True
        assert app.query_one("#composer").disabled is False
        await pilot.press("x")
        assert app.query_one("#composer").value == "x"
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.controller.busy is False
        assert app.controller.state.phase == "idle"


@pytest.mark.asyncio
async def test_tui_trace_cards_auto_collapse_and_can_toggle(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(TraceClient(), build_registry(base_dir=project), settings)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#composer").value = "inspect"
        await pilot.press("enter")
        await pilot.pause(0.5)
        trace = [item for item in app.controller.state.transcript if item.collapsible]
        assert trace and all(item.collapsed for item in trace)
        card = app.query(CollapsibleCard).first()
        assert card is not None
        assert await pilot.click(card)
        await pilot.pause()
        assert next(item for item in app.controller.state.transcript if item.id == trace[0].id).collapsed is False
        await pilot.press("shift+d")
        await pilot.pause()
        assert all(item.collapsed is True for item in app.controller.state.transcript if item.collapsible)


class ApprovalClient(LlmClient):
    """每次任务先请求 write_file 审批，决策落地后以正常回复收尾。"""

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        if any(message.role == "tool" for message in messages):
            yield StreamEvent(kind="content", text="写入完成")
        else:
            yield StreamEvent(
                kind="tool_call",
                tool_call=ToolCall(
                    id="call-write", name="write_file",
                    arguments='{"path": "note.txt", "content": "hi"}',
                ),
            )
        yield StreamEvent(kind="done")


def make_approval_context(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    agent = ReActAgent(
        ApprovalClient(),
        build_registry(base_dir=project),
        settings,
        approval_policy=HITLPolicy(enabled=True),
    )
    return agent, settings, manager, project


async def wait_for_condition(pilot, predicate, attempts: int = 200) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await pilot.pause(0.01)
    return predicate()


def approval_card_text(app) -> str:
    # 新旧卡片在异步移除窗口内可能短暂共存，断言最新一张
    cards = list(app.query(".inline-approval-card"))
    return str(cards[-1]._text.render())


@pytest.mark.asyncio
async def test_tui_approval_card_is_text_only_and_decides_via_composer(tmp_path):
    agent, settings, manager, project = make_approval_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        task = asyncio.create_task(app.controller.submit("写个文件"))
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_approval is not None
        )
        await pilot.pause(0.1)

        card = app.query_one(".inline-approval-card")
        assert not list(card.query(Button))
        assert not list(card.query(Input))
        text = approval_card_text(app)
        assert "write_file" in text
        assert "confirm" in text
        assert "y 批准" in text
        composer = app.query_one("#composer")
        assert app.focused is composer

        # 无效输入：不执行工具，保留审批状态和输入内容
        composer.value = "帮我直接写"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.controller.state.pending_approval is not None
        assert composer.value == "帮我直接写"
        assert not (project / "note.txt").exists()

        # y 批准后工具以原始参数执行
        composer.value = "y"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: (project / "note.txt").exists()
        )
        await asyncio.wait_for(task, 2)
        assert (project / "note.txt").read_text(encoding="utf-8") == "hi"
        await pilot.pause(0.1)
        assert app.controller.state.phase == "idle"
        assert app.controller.state.pending_approval is None
        assert app.query_one("#composer").placeholder == "输入任务或 /help …"


@pytest.mark.asyncio
async def test_tui_approval_edit_args_requires_valid_json_and_double_confirmation(tmp_path):
    agent, settings, manager, project = make_approval_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        task = asyncio.create_task(app.controller.submit("写个文件"))
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_approval is not None
        )

        composer = app.query_one("#composer")
        composer.value = "e"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app._decision_mode == "approval_edit"
        assert "完整 JSON" in approval_card_text(app)

        # 非法 JSON：继续等待 JSON，不执行工具
        composer.value = "not-json"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app._decision_mode == "approval_edit"
        assert app.controller.state.pending_approval is not None
        assert not (project / "note2.txt").exists()

        # 合法 JSON：先展示新参数，进入二次确认
        composer.value = '{"path": "note2.txt", "content": "new"}'
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app._decision_mode == "approval_confirm_modified"
        assert "note2.txt" in approval_card_text(app)
        assert not (project / "note2.txt").exists()

        # y 才把修改后的参数提交给当前工具调用
        composer.value = "y"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: (project / "note2.txt").exists()
        )
        await asyncio.wait_for(task, 2)
        assert (project / "note2.txt").read_text(encoding="utf-8") == "new"
        assert not (project / "note.txt").exists()
        assert app.controller.state.phase == "idle"


@pytest.mark.asyncio
async def test_tui_approval_escape_is_fail_closed(tmp_path):
    agent, settings, manager, project = make_approval_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        task = asyncio.create_task(app.controller.submit("写个文件"))
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_approval is not None
        )
        await pilot.press("escape")
        await asyncio.wait_for(task, 2)
        assert not (project / "note.txt").exists()
        assert app.controller.state.phase == "idle"
        assert app.controller.state.pending_approval is None


@pytest.mark.asyncio
async def test_tui_approval_modified_args_can_be_rejected(tmp_path):
    agent, settings, manager, project = make_approval_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        task = asyncio.create_task(app.controller.submit("写个文件"))
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_approval is not None
        )
        composer = app.query_one("#composer")
        composer.value = "e"
        await pilot.press("enter")
        composer.value = '{"path": "note2.txt", "content": "new"}'
        await pilot.press("enter")
        assert app._decision_mode == "approval_confirm_modified"

        # 二次确认拒绝：不执行修改后的调用
        composer.value = "r"
        await pilot.press("enter")
        await asyncio.wait_for(task, 2)
        assert not (project / "note2.txt").exists()
        assert not (project / "note.txt").exists()
        assert app.controller.state.phase == "idle"


@pytest.mark.asyncio
async def test_tui_ctrl_c_during_approval_cancels_without_executing(tmp_path):
    agent, settings, manager, project = make_approval_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        task = asyncio.create_task(app.controller.submit("写个文件"))
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_approval is not None
        )
        await pilot.press("ctrl+c")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert not (project / "note.txt").exists()
        assert app.controller.state.phase == "idle"


def make_memory_context(tmp_path: Path, *, seed: str = ""):
    project = tmp_path / "project"
    project.mkdir()
    manager = ConfigManager(user_dir=tmp_path / "user", project_dir=project, env={}, load_env=False)
    settings = Settings(provider="test", model="test-model", api_base="https://example.test", context_window=128_000)
    memory = MemoryManager(project)
    if seed:
        memory.save(seed)
    agent = ReActAgent(
        DummyClient(), build_registry(base_dir=project), settings, memory_manager=memory
    )
    return agent, settings, manager, memory, project


@pytest.mark.asyncio
async def test_tui_init_confirmation_via_composer(tmp_path):
    agent, settings, manager, memory, project = make_memory_context(tmp_path)
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        composer = app.query_one("#composer")
        composer.value = "/init"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is not None
        )
        await pilot.pause(0.1)

        card = app.query_one(".inline-confirmation-card")
        assert not list(card.query(Button))
        assert not list(card.query(Input))
        assert "写入项目记忆" in str(card._text.render())

        # n 取消：不写入文件
        composer.value = "n"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is None
        )
        assert not (project / "XG.md").exists()

        # 无效输入不写入，确认状态保留
        composer.value = "/init"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is not None
        )
        composer.value = "随便看看"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.controller.state.pending_confirmation is not None
        assert not (project / "XG.md").exists()

        # y 确认写入
        composer.value = "y"
        await pilot.press("enter")
        assert await wait_for_condition(pilot, lambda: (project / "XG.md").exists())
        assert app.controller.state.pending_confirmation is None
        await pilot.pause(0.1)
        assert composer.placeholder == "输入任务或 /help …"


@pytest.mark.asyncio
async def test_tui_memory_clear_requires_exact_word(tmp_path):
    agent, settings, manager, memory, project = make_memory_context(tmp_path, seed="记住用户偏好")
    app = XgTuiApp(agent, settings, manager)
    async with app.run_test(size=(120, 30)) as pilot:
        composer = app.query_one("#composer")
        composer.value = "/memory clear"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is not None
        )
        await pilot.pause(0.1)

        card = app.query_one(".inline-confirmation-card")
        assert not list(card.query(Button))
        assert not list(card.query(Input))
        assert "clear 确认清空" in str(card._text.render())
        assert "confirmation-memory_clear" in str(card.classes)

        # 单个 y 不是确认词，记忆保留
        composer.value = "y"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.controller.state.pending_confirmation is not None
        assert memory.count() == 1

        # cancel 取消
        composer.value = "cancel"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is None
        )
        assert memory.count() == 1

        # 精确输入 clear 才执行清空
        composer.value = "/memory clear"
        await pilot.press("enter")
        assert await wait_for_condition(
            pilot, lambda: app.controller.state.pending_confirmation is not None
        )
        composer.value = "clear"
        await pilot.press("enter")
        assert await wait_for_condition(pilot, lambda: memory.count() == 0)
        assert "已清空 1 条长期记忆" in app.controller.state.notification
