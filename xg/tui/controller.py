"""UI-independent orchestration for one interactive XG session."""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from xg.agent.plan import Plan, PlanEvent, PlanExecutor, ReviewDecision
from xg.agent.react import AgentEvent, ReActAgent
from xg.cli.commands import CommandContext, CommandResult, CommandService
from xg.cli.help import parse_help_command
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.safety.hitl import ApprovalDecision
from xg.tui.reducer import finalize_trace, reduce_agent_event, reduce_plan_event
from xg.tui.state import (
    ApprovalRequest,
    ConfirmationRequest,
    InspectorView,
    QueueItem,
    QueueItemKind,
    MemoryInspectorSnapshot,
    SafetyInspectorSnapshot,
    SessionInspectorSnapshot,
    TuiState,
    TranscriptItem,
    UsageSnapshot,
)


StateListener = Callable[[TuiState], None]
MAX_QUEUE_SIZE = 20


@dataclass(frozen=True)
class QueuedSubmission:
    id: str
    text: str
    kind: QueueItemKind


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
        policy = agent.approval_policy
        memory_snapshot = self._read_memory_snapshot(agent.memory_manager)
        self.state = TuiState(
            ui_language=settings.ui_language,
            inspector=replace(
                TuiState().inspector,
                session=SessionInspectorSnapshot(
                    provider=settings.provider,
                    model=settings.model,
                    status="Idle",
                ),
                memory=memory_snapshot,
                safety=SafetyInspectorSnapshot(
                    hitl_enabled=bool(policy and policy.enabled),
                    session_allow_all=bool(policy and policy.session_allow_all),
                ),
                provider=settings.provider,
                model=settings.model,
                usage=UsageSnapshot(context_window=settings.context_window),
                context_window=settings.context_window,
                hitl_enabled=bool(agent.approval_policy and agent.approval_policy.enabled),
            )
        )
        self.command_service = CommandService(CommandContext(agent, settings, manager))
        self._counter = itertools.count(1)
        self._queue_counter = itertools.count(1)
        self._queue: deque[QueuedSubmission] = deque()
        self._queue_worker: asyncio.Task | None = None
        self._shutting_down = False
        self._active_task: asyncio.Task | None = None
        self._active_turn_id = ""
        self._approval_future: asyncio.Future[ApprovalDecision] | None = None
        self._review_future: asyncio.Future[ReviewDecision] | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._confirmation_future: asyncio.Future[bool] | None = None
        mcp_manager = getattr(agent, "mcp_manager", None)
        if mcp_manager is not None:
            mcp_manager.add_listener(self._on_mcp_event)
        if agent.approval_policy is not None:
            agent.approval_policy.requester = self._request_approval

    @staticmethod
    def _read_memory_snapshot(memory, *, last_operation: str = "") -> MemoryInspectorSnapshot:
        if memory is None:
            return MemoryInspectorSnapshot(
                store_available=False,
                last_operation=last_operation or "unavailable",
            )
        try:
            project = memory.project_loader.load()
            sources = {section.source for section in project.sections}
            warnings = len(memory.warnings())
            try:
                count = memory.count()
            except Exception:
                count = 0
            return MemoryInspectorSnapshot(
                project_root=str(memory.project_root),
                xg_loaded="XG.md" in sources,
                xg_local_loaded="XG.local.md" in sources,
                warning_count=warnings,
                memory_count=count,
                store_available=memory.store is not None,
                last_operation=last_operation or "ready",
            )
        except Exception:
            return MemoryInspectorSnapshot(
                project_root=str(getattr(memory, "project_root", "")),
                store_available=False,
                last_operation=last_operation or "unavailable",
            )

    def set_inspector_view(self, view: InspectorView) -> bool:
        """Select an Inspector view without creating a transcript event."""
        if view not in ("session", "plan", "memory", "safety"):
            return False
        if self.state.inspector.active_view == view:
            return True
        self._set_state(replace(
            self.state,
            inspector=replace(self.state.inspector, active_view=view),
        ))
        return True

    def cycle_inspector_view(self, step: int = 1) -> None:
        views: tuple[InspectorView, ...] = ("session", "plan", "memory", "safety")
        try:
            index = views.index(self.state.inspector.active_view)
        except ValueError:
            index = 0
        self.set_inspector_view(views[(index + step) % len(views)])

    def snapshot(self) -> TuiState:
        return self.state

    @property
    def busy(self) -> bool:
        active = self._active_task is not None and not self._active_task.done()
        worker = self._queue_worker is not None and not self._queue_worker.done()
        return active or worker or self._confirmation is not None

    def _publish(self) -> None:
        if self.on_state_change is not None:
            self.on_state_change(self.state)

    def _set_state(self, state: TuiState) -> None:
        self.state = state
        self._publish()

    async def _on_mcp_event(self, event) -> None:
        """Expose MCP lifecycle/resource events without coupling MCP to Textual."""
        if self._shutting_down:
            return
        if event.kind == "mcp_server_unavailable":
            self._append_item(TranscriptItem(
                id=f"mcp-{event.server}-{len(self.state.transcript)}",
                kind="error",
                text=f"MCP Server {event.server} 不可用：{event.text}",
            ))
        elif event.kind == "mcp_resource_read":
            self._append_item(TranscriptItem(
                id=f"mcp-resource-{len(self.state.transcript)}",
                kind="context",
                text=f"已读取 MCP resource：{event.server}:{event.text}",
                collapsible=True,
                collapsed=True,
            ))
        elif event.kind == "mcp_server_ready":
            self._set_state(replace(
                self.state,
                notification=(
                    f"MCP Server {event.server} 已连接"
                    f"（{event.tool_count or 0} tools / {event.resource_count or 0} resources）"
                ),
                notification_level="info",
            ))

    def _begin_turn(self) -> str | None:
        # The queue worker may be alive while it is starting the next turn;
        # only an active turn itself prevents a new turn from beginning.
        if self._active_task is not None and not self._active_task.done():
            self._set_state(replace(self.state, notification="当前任务仍在运行，请先取消", notification_level="warning"))
            return None
        turn_id = f"turn-{next(self._counter)}"
        self._active_turn_id = turn_id
        self._set_state(replace(self.state, phase="running", active_turn_id=turn_id, notification=""))
        return turn_id

    @staticmethod
    def _submission_kind(text: str) -> QueueItemKind:
        lowered = text.lower()
        if lowered.startswith("/plan"):
            return "plan"
        if lowered.startswith("/"):
            return "command"
        return "task"

    def _publish_queue(self, *, notification: str | None = None, level: str = "info") -> None:
        queue_items = [
            QueueItem(id=item.id, text=item.text, kind=item.kind)
            for item in self._queue
        ]
        updates = {"queue": queue_items}
        if notification is not None:
            updates.update(notification=notification, notification_level=level)
        self._set_state(replace(self.state, **updates))

    def _enqueue(self, text: str) -> bool:
        if len(self._queue) >= MAX_QUEUE_SIZE:
            self._set_state(replace(
                self.state,
                notification=f"任务队列已满（最多 {MAX_QUEUE_SIZE} 项），请稍后再试",
                notification_level="warning",
            ))
            return False
        item = QueuedSubmission(
            id=f"queue-{next(self._queue_counter)}",
            text=text,
            kind=self._submission_kind(text),
        )
        self._queue.append(item)
        self._publish_queue(notification=f"已加入队列 #{item.id.removeprefix('queue-')}")
        return True

    def _ensure_queue_worker(self) -> None:
        if self._shutting_down or not self._queue:
            return
        if self._queue_worker is None or self._queue_worker.done():
            self._queue_worker = asyncio.create_task(self._drain_queue())

    async def _drain_queue(self) -> None:
        current = asyncio.current_task()
        try:
            while self._queue and not self._shutting_down:
                submission = self._queue.popleft()
                self._publish_queue(notification=f"开始执行队列 #{submission.id.removeprefix('queue-')}")
                turn_task = asyncio.create_task(self._execute_one(submission.text))
                try:
                    await turn_task
                    if self._confirmation_future is not None:
                        await self._confirmation_future
                except asyncio.CancelledError:
                    if self._shutting_down:
                        break
                except Exception as exc:
                    self._set_state(replace(
                        self.state,
                        notification=f"队列任务失败：{exc}",
                        notification_level="error",
                    ))
        finally:
            if self._queue_worker is current:
                self._queue_worker = None
            if self._shutting_down:
                self._queue.clear()
                self._publish_queue()

    async def submit(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        lowered = text.lower()
        if parse_help_command(text) is not None:
            result = await self.execute_command(text)
            if result.message:
                self._append_help(result.message)
            return result.ok
        if lowered in ("/cancel", "/c"):
            await self.cancel()
            return True
        if lowered in ("/exit", "/quit"):
            self._shutting_down = True
            await self.cancel()
            self._queue.clear()
            self._publish_queue(notification="正在退出，已清理等待队列")
            return True
        if self.state.phase == "awaiting_plan_review" and not self._is_language_command(text):
            self._set_state(replace(
                self.state,
                notification="当前处于计划审阅，请按 Enter 执行、r 重规划或 Esc 取消",
                notification_level="info",
            ))
            return False
        if self.busy and (self._is_readonly_mcp_command(text) or self._is_language_command(text)):
            result = await self.execute_command(text)
            if result.message:
                self._append_system(result.message)
            return result.ok
        if self.busy or self._queue:
            return self._enqueue(text)
        try:
            return await self._execute_one(text)
        finally:
            self._ensure_queue_worker()

    async def _execute_one(self, text: str) -> bool:
        lowered = text.lower()
        if text.startswith("/") and not lowered.startswith("/plan"):
            current = asyncio.current_task()
            self._active_task = current
            try:
                result = await self.execute_command(text)
                if result.message and not result.open_modal:
                    self._append_system(result.message)
                return True
            finally:
                if self._active_task is current:
                    self._active_task = None

        turn_id = self._begin_turn()
        if turn_id is None:
            return False
        self._append_item(TranscriptItem(id=f"user-{len(self.state.transcript)}", kind="user", text=text, turn_id=turn_id))
        is_plan = text.lower().startswith("/plan")
        goal = text[5:].strip() if is_plan else ""
        if is_plan and not goal:
            self._append_system("用法: /plan <任务描述>")
            return True
        if is_plan:
            progress_text = "正在生成执行计划"
        else:
            progress_text = "正在准备响应"
        self._append_item(TranscriptItem(
            id=f"progress-{turn_id}",
            kind="progress",
            progress_kind="plan" if is_plan else "response",
            text=progress_text,
            turn_id=turn_id,
            trace_id=turn_id,
            status="running",
        ))
        current = asyncio.current_task()
        self._active_task = current
        try:
            if is_plan:
                await self._run_plan(goal, turn_id)
            else:
                await self._run_agent(text, turn_id)
        except asyncio.CancelledError:
            cancelled = finalize_trace(self.state, turn_id, status="cancelled")
            self._set_state(replace(cancelled, phase="idle", pending_approval=None, pending_plan=None, notification="当前任务已取消"))
            raise
        finally:
            self._remove_progress(turn_id)
            if self._active_task is current:
                self._active_task = None
            self._approval_future = None
            self._review_future = None
            if self.state.active_turn_id == turn_id and self.state.phase == "running":
                self._set_state(replace(self.state, phase="idle", pending_approval=None, pending_plan=None))
        return True

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
            mcp_manager=getattr(self.agent, "mcp_manager", None),
        )
        async for event in executor.run(goal):
            self._set_state(reduce_plan_event(self.state, event, turn_id))

    async def cancel(self) -> bool:
        if self._confirmation is not None:
            await self.confirm_command(False)
            return True
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
        policy = self.agent.approval_policy
        self._set_state(replace(
            self.state,
            phase="awaiting_approval",
            pending_approval=request,
            inspector=replace(
                self.state.inspector,
                safety=replace(
                    self.state.inspector.safety,
                    hitl_enabled=bool(policy and policy.enabled),
                    session_allow_all=bool(policy and policy.session_allow_all),
                    approval_status="waiting",
                    current_tool=tool_name,
                    current_level=level,
                ),
            ),
        ))
        try:
            return await self._approval_future
        finally:
            self._approval_future = None

    async def approve_tool(self, decision: ApprovalDecision) -> None:
        if decision.reason == "user_approved_allow_all" and self.agent.approval_policy:
            self.agent.approval_policy.allow_all()
        policy = self.agent.approval_policy
        self._set_state(replace(
            self.state,
            inspector=replace(
                self.state.inspector,
                safety=replace(
                    self.state.inspector.safety,
                    hitl_enabled=bool(policy and policy.enabled),
                    session_allow_all=bool(policy and policy.session_allow_all),
                ),
            ),
        ))
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

    def toggle_plan_details(self) -> None:
        """Toggle the latest inline plan card without opening another screen."""
        items = list(self.state.transcript)
        for index in range(len(items) - 1, -1, -1):
            if items[index].kind == "plan":
                items[index] = replace(items[index], collapsed=not items[index].collapsed)
                self._set_state(replace(self.state, transcript=items))
                return

    def toggle_diagram_source(self) -> bool:
        """Toggle Mermaid source for the latest assistant diagram."""
        items = list(self.state.transcript)
        for index in range(len(items) - 1, -1, -1):
            item = items[index]
            if item.kind == "assistant" and "```mermaid" in item.text.lower():
                items[index] = replace(item, diagram_source_visible=not item.diagram_source_visible)
                self._set_state(replace(self.state, transcript=items))
                return True
        return False

    def toggle_trace_item(self, item_id: str) -> None:
        """Toggle one user-selected thinking/tool trace item."""
        items = list(self.state.transcript)
        for index, item in enumerate(items):
            if item.id == item_id and item.collapsible:
                expanded = item.collapsed
                items[index] = replace(
                    item,
                    collapsed=not expanded,
                    user_collapsed=not expanded,
                )
                self._set_state(replace(self.state, transcript=items))
                return

    def toggle_latest_trace(self) -> None:
        """Toggle the newest collapsible thinking/tool item."""
        for item in reversed(self.state.transcript):
            if item.collapsible:
                self.toggle_trace_item(item.id)
                return

    def toggle_trace_group(self) -> None:
        """Toggle all collapsible items belonging to the newest trace."""
        latest = next((item for item in reversed(self.state.transcript) if item.collapsible), None)
        if latest is None:
            return
        trace_id = latest.trace_id
        members = [item for item in self.state.transcript if item.collapsible and item.trace_id == trace_id]
        # Mixed state is treated as "collapse all"; only an entirely
        # collapsed group expands on the next group toggle.
        expand = all(item.collapsed for item in members)
        member_ids = {item.id for item in members}
        items = [
            replace(item, collapsed=not expand, user_collapsed=not expand)
            if item.id in member_ids else item
            for item in self.state.transcript
        ]
        self._set_state(replace(self.state, transcript=items))

    def refresh_diagrams(self) -> None:
        """Republish state so renderables recalculate their terminal layout."""
        self._publish()

    async def execute_command(self, raw: str) -> CommandResult:
        current = asyncio.current_task()
        is_help = parse_help_command(raw) is not None
        is_language = self._is_language_command(raw)
        if self.busy and self._active_task is not current and not self._is_readonly_mcp_command(raw) and not is_help and not is_language:
            return CommandResult(ok=False, message="当前任务正在运行，请通过输入提交命令以加入队列")
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
            request = ConfirmationRequest(
                "init",
                "写入项目记忆",
                f"已生成 XG.md 草稿，将写入项目根目录的 XG.md：\n\n{draft}",
                draft,
            )
            self._confirmation = request
            self._confirmation_future = asyncio.get_running_loop().create_future()
            self._set_state(replace(self.state, pending_confirmation=request, notification="请输入 y 确认写入 · n 取消"))
            return CommandResult(ok=True, open_modal="init")
        if lowered == "/memory clear" and memory is not None:
            try:
                count = memory.count()
            except Exception as exc:
                return CommandResult(ok=False, message=f"记忆操作失败：{exc}")
            if count == 0:
                return CommandResult(ok=True, message="当前项目没有长期记忆")
            request = ConfirmationRequest(
                "memory_clear",
                "清空长期记忆",
                f"当前项目有 {count} 条长期记忆。\n清空后不可通过 XG 恢复。",
            )
            self._confirmation = request
            self._confirmation_future = asyncio.get_running_loop().create_future()
            self._set_state(replace(self.state, pending_confirmation=request, notification="请输入 clear 确认清空长期记忆"))
            return CommandResult(ok=True, open_modal="memory_clear")
        result = await self.command_service.execute(raw)
        current_usage = self.state.inspector.usage
        if cleared:
            current_usage = UsageSnapshot(context_window=self.settings.context_window)
        elif self.settings.context_window != current_usage.context_window:
            current_usage = replace(
                current_usage,
                context_window=self.settings.context_window,
                window_ratio=(
                    current_usage.estimated_prompt_tokens / self.settings.context_window
                    if self.settings.context_window > 0 else 0.0
                ),
                budget_usage_ratio=(
                    current_usage.estimated_prompt_tokens / self.settings.token_budget
                    if self.settings.token_budget > 0 else 0.0
                ),
            )
        memory_snapshot = self.state.inspector.memory
        if lowered == "/init" or lowered == "/save" or lowered.startswith("/memory"):
            memory_snapshot = self._read_memory_snapshot(memory, last_operation=lowered)
        policy = self.agent.approval_policy
        self._set_state(replace(
            self.state,
            ui_language=self.settings.ui_language,
            transcript=[] if cleared else self.state.transcript,
            inspector=replace(
                self.state.inspector,
                session=replace(
                    self.state.inspector.session,
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
                memory=memory_snapshot,
                safety=replace(
                    self.state.inspector.safety,
                    hitl_enabled=bool(policy and policy.enabled),
                    session_allow_all=bool(policy and policy.session_allow_all),
                ),
                provider=self.settings.provider,
                model=self.settings.model,
                usage=current_usage,
                context_tokens=0 if cleared else self.state.inspector.context_tokens,
                context_window=self.settings.context_window,
                hitl_enabled=bool(self.agent.approval_policy and self.agent.approval_policy.enabled),
            ),
        ))
        return result

    @staticmethod
    def _is_language_command(raw: str) -> bool:
        parts = raw.strip().lower().split()
        return bool(parts and parts[0] in {"/lang", "/language"})

    @staticmethod
    def _is_readonly_mcp_command(raw: str) -> bool:
        parts = raw.strip().lower().split()
        return bool(
            parts
            and parts[0] == "/mcp"
            and (len(parts) == 1 or parts[1] in {"status", "logs", "resources"})
        )

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
        memory = getattr(self.agent, "memory_manager", None)
        operation = "init" if request.kind == "init" else "clear"
        self._set_state(replace(
            self.state,
            pending_confirmation=None,
            notification=message,
            notification_level="info",
            inspector=replace(
                self.state.inspector,
                memory=self._read_memory_snapshot(
                    memory,
                    last_operation=operation if confirmed else f"{operation}_cancelled",
                ),
            ),
        ))
        self._append_system(message)
        future = self._confirmation_future
        self._confirmation_future = None
        if future is not None and not future.done():
            future.set_result(confirmed)
        self._ensure_queue_worker()

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._confirmation is not None:
            await self.confirm_command(False)
        if self._active_task is not None and not self._active_task.done():
            await self.cancel()
        worker = self._queue_worker
        if worker is not None and not worker.done() and worker is not asyncio.current_task():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._queue.clear()
        self._publish_queue()
        mcp_manager = getattr(self.agent, "mcp_manager", None)
        if mcp_manager is not None:
            await mcp_manager.close()
        for service_name in ("web_search", "web_fetch"):
            service = getattr(self.agent, service_name, None)
            if service is not None:
                await service.close()

    async def startup(self) -> None:
        """Start configured MCP servers without blocking TUI construction."""
        mcp_manager = getattr(self.agent, "mcp_manager", None)
        if mcp_manager is None:
            return
        await mcp_manager.ensure_started()
        for error in mcp_manager.config_errors:
            self._append_system(f"MCP 配置提示：{error}")
        snapshots = mcp_manager.snapshots()
        if snapshots:
            ready = sum(item.status == "ready" for item in snapshots)
            self._set_state(replace(
                self.state,
                notification=f"MCP: {ready}/{len(snapshots)} 个 Server 可用",
                notification_level="info" if ready == len(snapshots) else "warning",
            ))

    def _append_item(self, item: TranscriptItem) -> None:
        state = replace(self.state, transcript=[*self.state.transcript, item])
        self._set_state(state)

    def _append_system(self, message: str) -> None:
        if message:
            self._append_item(TranscriptItem(id=f"system-{len(self.state.transcript)}", kind="system", text=message))

    def _append_help(self, message: str) -> None:
        if message:
            self._append_item(TranscriptItem(id=f"help-{len(self.state.transcript)}", kind="help", text=message))

    def _remove_progress(self, turn_id: str) -> None:
        """Remove the local waiting indicator for one active turn."""
        items = [
            item for item in self.state.transcript
            if not (item.kind == "progress" and item.turn_id == turn_id)
        ]
        if len(items) != len(self.state.transcript):
            self._set_state(replace(self.state, transcript=items))
