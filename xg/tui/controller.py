"""UI-independent orchestration for one interactive XG session."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import replace
from typing import Awaitable, Callable

from xg.agent.plan import Plan, PlanEvent, PlanExecutor, ReviewDecision
from xg.agent.react import AgentEvent, ReActAgent
from xg.cli.commands import CommandContext, CommandResult, CommandService
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.safety.hitl import ApprovalDecision
from xg.tui.reducer import reduce_agent_event, reduce_plan_event
from xg.tui.state import ApprovalRequest, ConfirmationRequest, TuiState, TranscriptItem


StateListener = Callable[[TuiState], None]


class SessionController:
    """Own the active turn and expose actions to any UI adapter.

    The controller deliberately has no Textual imports.  ``submit`` can be
    awaited by tests or run as an asyncio task by the TUI, while all event
    handling follows the same reducer path.
    """

    def __init__(
        self,
        agent: ReActAgent,
        settings: Settings,
        manager: ConfigManager,
        on_state_change: StateListener | None = None,
    ) -> None:
        self.agent = agent
        self.settings = settings
        self.manager = manager
        self.on_state_change = on_state_change
        self.state = TuiState(
            inspector=replace(
                TuiState().inspector,
                provider=settings.provider,
                model=settings.model,
                context_window=settings.context_window,
                hitl_enabled=bool(agent.approval_policy and agent.approval_policy.enabled),
            )
        )
        self.command_service = CommandService(CommandContext(agent, settings, manager))
        self._counter = itertools.count(1)
        self._active_task: asyncio.Task | None = None
        self._active_turn_id = ""
        self._approval_future: asyncio.Future[ApprovalDecision] | None = None
        self._review_future: asyncio.Future[ReviewDecision] | None = None
        self._confirmation: ConfirmationRequest | None = None
        if agent.approval_policy is not None:
            agent.approval_policy.requester = self._request_approval

    def snapshot(self) -> TuiState:
        return self.state

    @property
    def busy(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    def _publish(self) -> None:
        if self.on_state_change is not None:
            self.on_state_change(self.state)

    def _set_state(self, state: TuiState) -> None:
        self.state = state
        self._publish()

    def _begin_turn(self) -> str | None:
        if self.busy:
            self._set_state(replace(self.state, notification="当前任务仍在运行，请先取消", notification_level="warning"))
            return None
        turn_id = f"turn-{next(self._counter)}"
        self._active_turn_id = turn_id
        self._set_state(replace(self.state, phase="running", active_turn_id=turn_id, notification=""))
        return turn_id

    async def submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.startswith("/") and not text.lower().startswith("/plan"):
            if text.lower() in ("/cancel", "/c"):
                await self.cancel()
                return
            result = await self.execute_command(text)
            if result.message and not result.open_modal:
                self._append_system(result.message)
            if result.should_exit:
                return
            return

        turn_id = self._begin_turn()
        if turn_id is None:
            return
        self._append_item(TranscriptItem(id=f"user-{len(self.state.transcript)}", kind="user", text=text, turn_id=turn_id))
        current = asyncio.current_task()
        self._active_task = current
        try:
            if text.lower().startswith("/plan"):
                goal = text[5:].strip()
                if not goal:
                    self._append_system("用法: /plan <任务描述>")
                    return
                await self._run_plan(goal, turn_id)
            else:
                await self._run_agent(text, turn_id)
        except asyncio.CancelledError:
            self._set_state(replace(self.state, phase="idle", pending_approval=None, pending_plan=None, notification="当前任务已取消"))
            raise
        finally:
            if self._active_task is current:
                self._active_task = None
            self._approval_future = None
            self._review_future = None
            if self.state.active_turn_id == turn_id and self.state.phase == "running":
                self._set_state(replace(self.state, phase="idle", pending_approval=None, pending_plan=None))

    async def _run_agent(self, text: str, turn_id: str) -> None:
        async for event in self.agent.run(text):
            self._set_state(reduce_agent_event(self.state, event, turn_id))

    async def _run_plan(self, goal: str, turn_id: str) -> None:
        executor = PlanExecutor(
            llm=self.agent.llm,
            tools=self.agent.tools,
            settings=self.settings,
            reviewer=self._review_plan,
            approval_policy=self.agent.approval_policy,
            audit=self.agent.audit,
            memory_manager=self.agent.memory_manager,
        )
        async for event in executor.run(goal):
            self._set_state(reduce_plan_event(self.state, event, turn_id))

    async def cancel(self) -> bool:
        task = self._active_task
        if task is None or task.done():
            self._set_state(replace(self.state, notification="当前没有运行中的任务", notification_level="info"))
            return False
        if self._approval_future and not self._approval_future.done():
            self._approval_future.set_result(ApprovalDecision(allow=False, reason="user_cancelled"))
        if self._review_future and not self._review_future.done():
            self._review_future.set_result(ReviewDecision(action="cancel"))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    async def _request_approval(self, tool_name: str, level: str, args: dict) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        self._approval_future = loop.create_future()
        request = ApprovalRequest(tool_name=tool_name, level=level, args=args, turn_id=self._active_turn_id)
        self._set_state(replace(self.state, phase="awaiting_approval", pending_approval=request))
        try:
            return await self._approval_future
        finally:
            self._approval_future = None

    async def approve_tool(self, decision: ApprovalDecision) -> None:
        if decision.reason == "user_approved_allow_all" and self.agent.approval_policy:
            self.agent.approval_policy.allow_all()
        future = self._approval_future
        if future is not None and not future.done():
            future.set_result(decision)

    async def _review_plan(self, plan: Plan) -> ReviewDecision:
        loop = asyncio.get_running_loop()
        self._review_future = loop.create_future()
        self._set_state(replace(self.state, phase="awaiting_plan_review", pending_plan=plan))
        try:
            return await self._review_future
        finally:
            self._review_future = None

    async def review_plan(self, decision: ReviewDecision) -> None:
        future = self._review_future
        if future is not None and not future.done():
            future.set_result(decision)

    async def execute_command(self, raw: str) -> CommandResult:
        if raw.strip().lower() in ("/cancel", "/c"):
            await self.cancel()
            return CommandResult(ok=True, message="已取消当前任务")
        cleared = raw.strip().lower() == "/clear"
        if cleared:
            self.agent.clear()
        lowered = raw.strip().lower()
        memory = getattr(self.agent, "memory_manager", None)
        if lowered == "/init" and memory is not None:
            try:
                draft = await memory.generate_init_draft(self.agent.llm)
            except Exception as exc:
                return CommandResult(ok=False, message=f"生成 XG.md 失败：{exc}")
            request = ConfirmationRequest("init", "写入项目记忆？", draft, draft)
            self._confirmation = request
            self._set_state(replace(self.state, pending_confirmation=request, notification="请确认写入 XG.md"))
            return CommandResult(ok=True, open_modal="init")
        if lowered == "/memory clear" and memory is not None:
            try:
                count = memory.count()
            except Exception as exc:
                return CommandResult(ok=False, message=f"记忆操作失败：{exc}")
            if count == 0:
                return CommandResult(ok=True, message="当前项目没有长期记忆")
            request = ConfirmationRequest("memory_clear", "清空长期记忆？", f"将删除当前项目的 {count} 条长期记忆。")
            self._confirmation = request
            self._set_state(replace(self.state, pending_confirmation=request, notification="请确认清空长期记忆"))
            return CommandResult(ok=True, open_modal="memory_clear")
        result = await self.command_service.execute(raw)
        self._set_state(replace(
            self.state,
            transcript=[] if cleared else self.state.transcript,
            inspector=replace(
                self.state.inspector,
                provider=self.settings.provider,
                model=self.settings.model,
                hitl_enabled=bool(self.agent.approval_policy and self.agent.approval_policy.enabled),
            ),
        ))
        return result

    async def confirm_command(self, confirmed: bool) -> None:
        request = self._confirmation
        self._confirmation = None
        if request is None:
            return
        message = "已取消操作"
        if confirmed:
            try:
                memory = self.agent.memory_manager
                if request.kind == "init":
                    path = memory.write_init_draft(str(request.payload))
                    message = f"已生成项目记忆：{path.name}"
                elif request.kind == "memory_clear":
                    removed = memory.clear()
                    message = f"已清空 {removed} 条长期记忆"
            except Exception as exc:
                message = f"操作失败：{exc}"
        self._set_state(replace(self.state, pending_confirmation=None, notification=message, notification_level="info"))
        self._append_system(message)

    async def shutdown(self) -> None:
        if self.busy:
            await self.cancel()

    def _append_item(self, item: TranscriptItem) -> None:
        state = replace(self.state, transcript=[*self.state.transcript, item])
        self._set_state(state)

    def _append_system(self, message: str) -> None:
        if message:
            self._append_item(TranscriptItem(id=f"system-{len(self.state.transcript)}", kind="system", text=message))
