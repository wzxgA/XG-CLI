"""第 10 期 Team MVP 测试。"""

from __future__ import annotations

import json
import re
from io import StringIO
from typing import AsyncIterator

from xg.agent.plan import ReviewDecision
from xg.agent.react import AgentEvent
from xg.agent.team import (
    AgentProfile,
    ReviewResult,
    TeamExecutor,
    TeamTask,
    conflict_safe_batches,
    parse_team_tasks,
)
from xg.llm.client import LlmClient
from xg.llm.types import StreamEvent, ToolCall, ToolResult
from xg.tool.builtin import build_registry
from xg.tui.reducer import reduce_team_event
from xg.tui.renderables import agent_group_renderable
from xg.tui.state import AgentGroupState, TuiState
from xg.tui.widgets.agent_group_card import AgentGroupCard


TEAM_PLAN = json.dumps({"tasks": [
    {
        "id": "t1", "title": "写入 A", "description": "用 write_file 写 a.txt",
        "deps": [], "owner_role": "coder", "allowed_tools": ["write_file"],
        "resource_claims": [{"pattern": "a.txt", "access": "write"}],
        "acceptance_criteria": ["a.txt 存在"],
    },
    {
        "id": "t2", "title": "写入 B", "description": "用 write_file 写 b.txt",
        "deps": [], "owner_role": "coder", "allowed_tools": ["write_file"],
        "resource_claims": [{"pattern": "b.txt", "access": "write"}],
        "acceptance_criteria": ["b.txt 存在"],
    },
    {
        "id": "t3", "title": "读取验证", "description": "用 read_file 验证两个文件",
        "deps": ["t1", "t2"], "owner_role": "tester", "allowed_tools": ["read_file"],
        "resource_claims": [
            {"pattern": "a.txt", "access": "read"},
            {"pattern": "b.txt", "access": "read"},
        ],
        "acceptance_criteria": ["两个文件内容可读取"],
    },
]}, ensure_ascii=False)


class TeamScriptClient(LlmClient):
    def __init__(self, worker_scripts: dict[str, list], reviews: list[str] | None = None):
        self.worker_scripts = {key: list(value) for key, value in worker_scripts.items()}
        self.reviews = list(reviews or [])
        self.requests: list[list] = []

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        self.requests.append(list(messages))
        system = messages[0].content if messages else ""
        if "团队任务规划器" in system:
            yield StreamEvent(kind="content", text=TEAM_PLAN)
            yield StreamEvent(kind="done")
            return
        if "严格的任务审查 Agent" in system:
            yield StreamEvent(kind="content", text=self.reviews.pop(0))
            yield StreamEvent(kind="done")
            return

        user = next(message.content for message in messages if message.role == "user")
        match = re.search(r"任务 (t\d+(?:-repair-\d+)?)", user)
        assert match is not None, user
        task_id = match.group(1)
        entry = self.worker_scripts[task_id].pop(0)
        content, calls = entry
        if content:
            yield StreamEvent(kind="content", text=content)
        for name, args in calls:
            yield StreamEvent(
                kind="tool_call",
                tool_call=ToolCall(id=f"call-{task_id}-{name}", name=name, arguments=args),
            )
        yield StreamEvent(kind="done", finish_reason="tool_calls" if calls else "stop")


async def approve_team(plan) -> ReviewDecision:
    return ReviewDecision(action="execute")


async def collect(executor: TeamExecutor, goal: str) -> list:
    return [event async for event in executor.run(goal)]


async def test_team_parallel_workers_artifacts_and_reviews(tmp_path, settings):
    client = TeamScriptClient(
        worker_scripts={
            "t1": [
                ("", [("write_file", json.dumps({"path": "a.txt", "content": "A"}))]),
                ("A 已写入", []),
            ],
            "t2": [
                ("", [("write_file", json.dumps({"path": "b.txt", "content": "B"}))]),
                ("B 已写入", []),
            ],
            "t3": [
                ("", [("read_file", json.dumps({"path": "a.txt"}))]),
                ("验证完成", []),
            ],
        },
        reviews=['{"verdict":"pass","findings":[],"required_fixes":[],"evidence":["ok"]}'] * 3,
    )
    registry = build_registry(base_dir=tmp_path)
    executor = TeamExecutor(
        llm=client, tools=registry, settings=settings,
        reviewer=approve_team, project_root=tmp_path,
    )

    events = await collect(executor, "写入并验证两个文件")

    assert events[-1].kind == "team_done"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B"
    first_batch = next(event for event in events if event.kind == "batch_started")
    assert first_batch.batch == ["t1", "t2"]
    assert len([event for event in events if event.kind == "artifact_produced"]) >= 5
    reviews = [event for event in events if event.kind == "task_review_done"]
    assert len(reviews) == 3 and all(event.review.verdict == "pass" for event in reviews)
    assert any("你的角色：coder" in message[0].content for message in client.requests)


async def test_team_failed_review_creates_targeted_repair(tmp_path, settings):
    client = TeamScriptClient(
        worker_scripts={
            "t1": [("初始实现", [])],
            "t1-repair-1": [("已修复", [])],
        }
    )
    review_count = 0

    async def review(task: TeamTask, artifacts):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return ReviewResult(task.id, "fail", ["密码校验错误"], ["修复密码校验"], [])
        return ReviewResult(task.id, "pass", [], [], ["修复后测试通过"])

    plan = json.dumps({"tasks": [{
        "id": "t1", "title": "登录实现", "description": "实现登录",
        "deps": [], "owner_role": "coder", "acceptance_criteria": ["密码校验正确"],
    }]}, ensure_ascii=False)
    client.worker_scripts["t1"] = [("初始实现", [])]

    # 替换规划响应为单任务，避免把测试重点绑定到固定示例计划。
    original = client.stream_chat

    async def stream_chat(messages, tools=None):
        if messages and "团队任务规划器" in messages[0].content:
            yield StreamEvent(kind="content", text=plan)
            yield StreamEvent(kind="done")
            return
        async for event in original(messages, tools):
            yield event

    client.stream_chat = stream_chat  # type: ignore[method-assign]
    executor = TeamExecutor(
        llm=client, tools=build_registry(base_dir=tmp_path), settings=settings,
        reviewer=approve_team, task_reviewer=review, project_root=tmp_path,
    )
    events = await collect(executor, "实现登录")

    assert events[-1].kind == "team_done"
    assert any(event.kind == "repair_requested" for event in events)
    assert [event.kind for event in events].count("task_review_done") == 2
    assert any(event.role == "repairer" for event in events if event.kind == "agent_started")


def test_team_parser_and_resource_conflict_scheduling():
    tasks, warnings = parse_team_tasks(TEAM_PLAN)
    assert not warnings
    assert [task.owner_role for task in tasks] == ["coder", "coder", "tester"]
    assert conflict_safe_batches(tasks) == [["t1", "t2"], ["t3"]]

    conflicting = [
        TeamTask("a", "A", "", [], resource_claims=[]),
        TeamTask("b", "B", "", [], resource_claims=[]),
    ]
    assert conflict_safe_batches(conflicting) == [["a"], ["b"]]


def test_profile_tool_intersection_is_explicit():
    profile = AgentProfile("reviewer", "", ("read_file",), is_reviewer=True)
    assert profile.allowed_tools == ("read_file",)


def test_team_events_keep_role_and_task_progress_in_tui_state():
    tasks, _ = parse_team_tasks(json.dumps({"tasks": [{
        "id": "t1", "title": "实现", "description": "实现代码", "deps": [],
        "owner_role": "coder", "acceptance_criteria": ["完成"],
    }]}))
    from xg.agent.team import TeamPlan, TeamEvent

    plan = TeamPlan("demo", tasks, [["t1"]])
    state = TuiState(active_turn_id="turn-1")
    state = reduce_team_event(state, TeamEvent("team_plan_generated", team_id="team-1", plan=plan), "turn-1")
    assert state.phase == "awaiting_plan_review"
    state = reduce_team_event(state, TeamEvent("task_started", team_id="team-1", plan=plan, task=tasks[0], role="coder"), "turn-1")
    assert state.plan_tasks["t1"] == "running"
    assert any("coder/t1" in item.text for item in state.transcript)


def test_team_agent_events_are_isolated_in_default_collapsed_groups():
    from xg.agent.team import TeamEvent, TeamPlan

    tasks, _ = parse_team_tasks(json.dumps({"tasks": [
        {"id": "t1", "title": "实现 A", "description": "实现 A", "deps": []},
        {"id": "t2", "title": "实现 B", "description": "实现 B", "deps": []},
    ]}))
    plan = TeamPlan("demo", tasks, [["t1", "t2"]])
    state = TuiState(active_turn_id="turn-1")

    state = reduce_team_event(
        state, TeamEvent("team_plan_generated", team_id="team-1", plan=plan), "turn-1"
    )
    for task, agent_id in zip(tasks, ("agent-a", "agent-b")):
        state = reduce_team_event(
            state,
            TeamEvent("agent_started", team_id="team-1", plan=plan, task=task,
                      agent_id=agent_id, role="coder"),
            "turn-1",
        )
        state = reduce_team_event(
            state,
            TeamEvent(
                "subtask_event", team_id="team-1", plan=plan, task=task,
                agent_id=agent_id, role="coder",
                agent_event=AgentEvent(kind="thinking", text=f"检查 {task.id}"),
            ),
            "turn-1",
        )

    assert state.agent_group_order == ["team-1:agent-a", "team-1:agent-b"]
    assert all(group.collapsed for group in state.agent_groups.values())
    assert state.agent_groups["team-1:agent-a"].entries[0].text == "检查 t1"
    assert state.agent_groups["team-1:agent-b"].entries[0].text == "检查 t2"
    assert [item.kind for item in state.transcript].count("agent_group") == 2


def test_team_repair_and_review_have_separate_group_identities():
    from xg.agent.team import TeamEvent, TeamPlan

    tasks, _ = parse_team_tasks(json.dumps({"tasks": [{
        "id": "t1", "title": "实现登录", "description": "实现登录", "deps": [],
    }]}))
    plan = TeamPlan("demo", tasks, [["t1"]])
    repair = TeamTask("t1-repair-1", "修复登录", "修复问题", [], owner_role="repairer")
    state = TuiState(active_turn_id="turn-1")

    state = reduce_team_event(
        state,
        TeamEvent("task_review_started", team_id="team-1", plan=plan, task=tasks[0], role="reviewer"),
        "turn-1",
    )
    state = reduce_team_event(
        state,
        TeamEvent(
            "task_review_done", team_id="team-1", plan=plan, task=tasks[0], role="reviewer",
            review=ReviewResult("t1", "fail", ["需要修复"], ["修复"], []),
        ),
        "turn-1",
    )
    state = reduce_team_event(
        state,
        TeamEvent("repair_requested", team_id="team-1", plan=plan, task=repair, role="repairer", message="修复"),
        "turn-1",
    )
    state = reduce_team_event(
        state,
        TeamEvent("agent_started", team_id="team-1", plan=plan, task=repair,
                  agent_id="agent-repair", role="repairer"),
        "turn-1",
    )
    state = reduce_team_event(
        state,
        TeamEvent(
            "subtask_event", team_id="team-1", plan=plan, task=repair,
            agent_id="agent-repair", role="repairer",
            agent_event=AgentEvent(kind="tool_result", tool_result=ToolResult(
                tool_call_id="call-1", name="read_file", ok=True, output="ok"
            )),
        ),
        "turn-1",
    )

    assert "team-1:reviewer:t1" in state.agent_groups
    assert "team-1:agent-repair" in state.agent_groups
    assert state.agent_groups["team-1:agent-repair"].task_id == "t1-repair-1"
    assert state.agent_groups["team-1:reviewer:t1"].status == "failed"
    assert any(item.kind == "agent_group" for item in state.transcript)


def test_agent_group_card_starts_collapsed_and_uses_group_identity():
    group = AgentGroupState(
        group_id="team-1:agent-a", team_id="team-1", agent_id="agent-a",
        role="coder", task_id="t1", task_title="实现登录", status="running",
    )
    card = AgentGroupCard(group)

    assert card.group_id == "team-1:agent-a"
    assert card.group.collapsed is True
    output = StringIO()
    from rich.console import Console
    Console(file=output, width=120).print(agent_group_renderable(group))
    assert "coder/t1" in output.getvalue()
