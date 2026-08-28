"""Textual fullscreen application for XG."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import Static

from xg.agent.plan import ReviewDecision
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.input_history import HistoryConfig, InputHistory
from xg.safety.hitl import ApprovalDecision
from xg.tui.controller import SessionController
from xg.tui.messages import (
    CommandSuggestionSelected,
    InspectorViewSelected,
    StateChanged,
    TraceCardToggled,
)
from xg.tui.state import TuiState
from xg.tui.widgets.action_card import InlineApprovalCard
from xg.tui.widgets.command_suggestions import CommandSuggestions
from xg.tui.widgets.composer import Composer
from xg.tui.widgets.footer import FooterBar
from xg.tui.widgets.header import HeaderBar
from xg.tui.widgets.inspector import InspectorPanel
from xg.tui.widgets.queue_status import QueueStatus
from xg.tui.widgets.transcript import TranscriptView


class XgTuiApp(App[None]):
    """One active session, with all execution delegated to the controller."""

    TITLE = "XG"
    CSS_PATH = "theme.tcss"
    BINDINGS = [
        ("enter", "plan_execute", "execute plan"),
        ("d", "plan_details", "plan details"),
        ("shift+d", "trace_group", "trace details"),
        ("r", "plan_replan", "replan"),
        ("escape", "escape", "cancel or clear"),
        ("ctrl+c", "cancel_turn", "取消当前任务"),
        ("ctrl+l", "clear_transcript", "清屏"),
        ("ctrl+r", "toggle_inspector", "侧栏"),
        ("ctrl+1", "inspector_session", "Inspector Session"),
        ("ctrl+2", "inspector_plan", "Inspector Plan"),
        ("ctrl+3", "inspector_memory", "Inspector Memory"),
        ("ctrl+4", "inspector_safety", "Inspector Safety"),
        ("ctrl+tab", "inspector_next", "下一个 Inspector 视图"),
        ("ctrl+shift+tab", "inspector_previous", "上一个 Inspector 视图"),
        ("f1", "show_help", "帮助"),
    ]

    def __init__(self, agent, settings: Settings, manager: ConfigManager) -> None:
        super().__init__()
        self.settings = settings
        self.input_history = getattr(agent, "input_history", None) or InputHistory(
            project_root=manager.project_dir.parent,
            user_dir=manager.user_dir,
            config=HistoryConfig(
                enabled=settings.input_history_enabled,
                persist=settings.input_history_persist,
                max_entries=settings.input_history_max_entries,
                max_entry_chars=settings.input_history_max_chars,
                max_file_bytes=settings.input_history_max_bytes,
            ),
        )
        agent.input_history = self.input_history
        self.controller = SessionController(agent, settings, manager, self._on_state_change)
        self._state = self.controller.snapshot()
        self._pending_render_state: TuiState | None = None
        self._render_timer: Timer | None = None
        self._modal_kind = ""
        self._replan_mode = False
        # 决策输入模式："" 等待决策 / "approval_edit" 修改参数 /
        # "approval_confirm_modified" 修改后二次确认
        self._decision_mode = ""
        self._modified_args: dict | None = None

    def compose(self) -> ComposeResult:
        # The work area owns the left brand/transcript column and the right
        # inspector.  Footer and composer deliberately remain outside it so
        # they span the full terminal width.
        with Horizontal(id="shell"):
            with Vertical(id="main-column"):
                yield HeaderBar(id="header")
                yield TranscriptView(id="transcript")
            yield InspectorPanel(id="inspector")

        yield FooterBar()
        with Vertical(id="composer-area"):
            yield Static("", id="notification")
            yield QueueStatus(id="queue-status")
            yield CommandSuggestions()
            yield Static("输入", id="composer-label")
            yield Composer()

    def on_mount(self) -> None:
        self._render_state(self._state)
        composer = self.query_one("#composer", Composer)
        composer.set_input_history(self.input_history)
        composer.focus()
        self.run_worker(self.controller.startup(), exclusive=False, name="mcp-startup")

    def _on_state_change(self, state: TuiState) -> None:
        self.post_message(StateChanged(state))

    def on_state_changed(self, message: StateChanged) -> None:
        self._state = message.state
        self._pending_render_state = message.state
        self._schedule_render()

    def _schedule_render(self) -> None:
        if self._render_timer is not None or not self.is_attached:
            return
        interval = 1 / max(1, self.settings.tui_refresh_fps)
        self._render_timer = self.set_timer(interval, self._flush_render)

    def _flush_render(self) -> None:
        self._render_timer = None
        state = self._pending_render_state
        self._pending_render_state = None
        if state is not None and self.is_attached:
            self._render_state(state)
        if self._pending_render_state is not None:
            self._schedule_render()

    def on_trace_card_toggled(self, message: TraceCardToggled) -> None:
        self.controller.toggle_trace_item(message.item_id)

    def on_inspector_view_selected(self, message: InspectorViewSelected) -> None:
        self.controller.set_inspector_view(message.view)

    def on_command_suggestion_selected(self, message: CommandSuggestionSelected) -> None:
        self.complete_command_suggestion(message.command)

    def complete_command_suggestion(self, command: str) -> None:
        """Replace only the leading command token and keep Composer focused."""
        composer = self.query_one("#composer", Composer)
        raw = composer.value
        leading = raw.lstrip()
        if not leading.startswith("/"):
            return
        token = leading.split(maxsplit=1)[0]
        prefix_length = len(raw) - len(leading)
        composer.value = raw[:prefix_length] + command + raw[prefix_length + len(token):]
        composer.cursor_position = len(composer.value)
        composer.focus()

    def _render_state(self, state: TuiState) -> None:
        self.query_one("#header", HeaderBar).update_state(state)
        self.query_one("#transcript", TranscriptView).update_state(state)
        self.query_one("#inspector", InspectorPanel).update_state(state)
        note = self.query_one("#notification", Static)
        note.update(state.notification)
        note.display = bool(state.notification)
        self.query_one("#queue-status", QueueStatus).update_state(state)
        composer = self.query_one("#composer", Composer)
        if state.pending_approval is not None:
            # Cards are read-only; the decision is typed into the Composer.
            # remove_children() detaches asynchronously, so during a rebuild
            # two cards can coexist briefly — always restyle the newest one.
            if self._decision_mode:
                cards = list(self.query(InlineApprovalCard))
                if cards:
                    cards[-1].set_mode(self._decision_mode, self._modified_args)
            composer.focus()
        elif state.pending_confirmation is not None:
            composer.focus()
        else:
            self._decision_mode = ""
            self._modified_args = None
        suggestions = self.query_one("#command-suggestions", CommandSuggestions)
        suggestions_allowed = (
            not self._replan_mode
            and state.phase != "awaiting_plan_review"
            and state.pending_approval is None
            and state.pending_confirmation is None
            and self._modal_kind == ""
        )
        composer.set_suggestions_enabled(suggestions_allowed)
        if not suggestions_allowed:
            suggestions.close()
        if state.pending_approval is not None:
            if self._decision_mode == "approval_edit":
                composer.placeholder = "修改参数：请输入完整 JSON，Esc 取消修改"
            elif self._decision_mode == "approval_confirm_modified":
                composer.placeholder = "输入 y 确认执行 · r 拒绝执行"
            else:
                composer.placeholder = "审批中：y 批准 · a 全部放行 · r 拒绝 · s 跳过 · e 修改参数"
        elif state.pending_confirmation is not None:
            if state.pending_confirmation.kind == "memory_clear":
                composer.placeholder = "确认中：输入 clear 确认清空，其他输入取消"
            else:
                composer.placeholder = "确认中：输入 y 确认写入 · n 取消"
        elif self._replan_mode:
            composer.placeholder = "输入重新规划要求，Enter 提交"
        elif state.phase == "awaiting_plan_review" and state.pending_plan is not None:
            composer.placeholder = "按 Enter 执行 · r 重规划 · Esc 取消"
        else:
            composer.placeholder = "输入任务或 /help …"

    def _render_state_immediately(self, state: TuiState) -> None:
        """Render an explicit local UI action without an older queued state."""
        if self._render_timer is not None:
            self._render_timer.stop()
            self._render_timer = None
        self._pending_render_state = None
        self._render_state(state)

    # 决策文本协议：等待审批时只接受明确命令，其他输入不执行工具。
    APPROVAL_TEXT_COMMANDS = {
        "": "approve",
        "y": "approve",
        "yes": "approve",
        "approve": "approve",
        "a": "allow-all",
        "allow-all": "allow-all",
        "allow_all": "allow-all",
        "r": "reject",
        "n": "reject",
        "no": "reject",
        "reject": "reject",
        "s": "skip",
        "skip": "skip",
    }

    def handle_inline_approval(self, command: str, modified_args: dict | None = None) -> None:
        decisions = {
            "approve": ApprovalDecision(allow=True, reason="user_approved"),
            "allow-all": ApprovalDecision(allow=True, reason="user_approved_allow_all"),
            "allow_all": ApprovalDecision(allow=True, reason="user_approved_allow_all"),
            "skip": ApprovalDecision(allow=False, reason="user_skipped"),
        }
        if command == "approve" and modified_args is not None:
            decision = ApprovalDecision(allow=True, args=modified_args, reason="user_modified")
        else:
            decision = decisions.get(command, ApprovalDecision(allow=False, reason="user_rejected"))
        self._decision_mode = ""
        self._modified_args = None
        asyncio.create_task(self.controller.approve_tool(decision))
        self.query_one("#composer", Composer).focus()

    def handle_inline_confirmation(self, confirmed: bool) -> None:
        self._decision_mode = ""
        self._modified_args = None
        asyncio.create_task(self.controller.confirm_command(confirmed))
        self.query_one("#composer", Composer).focus()

    def _show_decision_hint(self, message: str) -> None:
        """Surface a decision hint without touching controller state."""
        self._state = replace(
            self.controller.snapshot(),
            notification=message,
            notification_level="warning",
        )
        self._render_state_immediately(self._state)

    def _route_decision_input(self, text: str, composer: Composer) -> bool:
        """Consume the Composer text as a decision; True means handled.

        Decision states take priority over normal submissions so text like
        ``y`` or ``clear`` is never sent to the Agent as a user message.
        """
        state = self.controller.state
        if self._decision_mode == "approval_edit":
            self._handle_modified_args_json(text, composer)
            return True
        if self._decision_mode == "approval_confirm_modified":
            self._handle_modified_args_confirmation(text, composer)
            return True
        if state.pending_approval is not None:
            self._handle_approval_decision(text, composer)
            return True
        if state.pending_confirmation is not None:
            self._handle_confirmation_decision(state.pending_confirmation.kind, text, composer)
            return True
        return False

    def _handle_approval_decision(self, text: str, composer: Composer) -> None:
        lowered = text.lower()
        if lowered == "e":
            composer.value = ""
            self._decision_mode = "approval_edit"
            self._show_decision_hint("参数修改：请输入完整 JSON，Esc 取消修改")
            return
        command = self.APPROVAL_TEXT_COMMANDS.get(lowered)
        if command is None:
            # 无效输入保留在输入框，审批状态不变，工具不执行。
            self._show_decision_hint("审批中仅接受 y / a / r / s / e，本次输入未执行任何操作")
            return
        composer.value = ""
        self.handle_inline_approval(command)

    def _handle_modified_args_json(self, text: str, composer: Composer) -> None:
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            self._show_decision_hint("JSON 无效：请输入完整的 JSON 对象，工具未执行")
            return
        self._modified_args = parsed
        self._decision_mode = "approval_confirm_modified"
        composer.value = ""
        self._show_decision_hint("参数已修改，输入 y 确认执行 · r 拒绝执行")

    def _handle_modified_args_confirmation(self, text: str, composer: Composer) -> None:
        lowered = text.lower()
        if lowered in ("y", "yes"):
            composer.value = ""
            self.handle_inline_approval("approve", self._modified_args)
            return
        if lowered in ("r", "n", "no"):
            composer.value = ""
            self.handle_inline_approval("reject")
            return
        self._show_decision_hint("输入 y 确认执行修改后的参数 · r 拒绝")

    def _handle_confirmation_decision(self, kind: str, text: str, composer: Composer) -> None:
        lowered = text.lower()
        if kind == "memory_clear":
            if lowered == "clear":
                composer.value = ""
                self.handle_inline_confirmation(True)
                return
            if lowered in ("cancel", "n", "no"):
                composer.value = ""
                self.handle_inline_confirmation(False)
                return
            self._show_decision_hint("清空长期记忆需精确输入 clear，本次输入未生效")
            return
        if lowered in ("y", "yes"):
            composer.value = ""
            self.handle_inline_confirmation(True)
            return
        if lowered in ("n", "no", "cancel"):
            composer.value = ""
            self.handle_inline_confirmation(False)
            return
        self._show_decision_hint("输入 y 确认写入 · n 取消，本次输入未生效")

    def on_input_submitted(self, event: Composer.Submitted) -> None:
        text = event.value.strip()
        if self._route_decision_input(text, event.input):
            return
        if self._has_pending_plan() and not self._replan_mode:
            self._state = replace(
                self.controller.snapshot(),
                notification="当前处于计划审阅，请按 Enter 执行、r 重规划或 Esc 取消",
                notification_level="info",
            )
            self._render_state(self._state)
            return
        event.input.value = ""
        if text:
            if self._replan_mode:
                self._replan_mode = False
                event.input.placeholder = "输入任务或 /help …"
                asyncio.create_task(
                    self.controller.review_plan(ReviewDecision(action="replan", feedback=text))
                )
            else:
                asyncio.create_task(self._submit_text(text))

    async def _submit_text(self, text: str) -> None:
        try:
            accepted = await self.controller.submit(text)
            composer = self.query_one("#composer", Composer)
            composer.record_submission(text, accepted)
            if not accepted:
                if not composer.value:
                    composer.value = text
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.notify(f"任务失败：{exc}", severity="error")
        finally:
            if text.lower() in ("/exit", "/quit"):
                self.exit()

    async def action_cancel_turn(self) -> None:
        await self.controller.cancel()

    async def action_clear_transcript(self) -> None:
        await self.controller.submit("/clear")
        self._state = self.controller.snapshot()
        self._render_state_immediately(self._state)

    def _has_pending_plan(self) -> bool:
        return self.controller.state.phase == "awaiting_plan_review" and self.controller.state.pending_plan is not None

    def _has_detail_toggle_target(self) -> bool:
        return any(
            item.collapsible
            or (item.kind == "assistant" and "```mermaid" in item.text.lower())
            for item in self.controller.state.transcript
        )

    def action_plan_execute(self) -> None:
        if self._has_pending_plan() and not self._replan_mode:
            self.query_one("#composer", Composer).value = ""
            asyncio.create_task(self.controller.review_plan(ReviewDecision(action="execute")))

    def action_plan_details(self) -> None:
        if self._has_pending_plan() and not self._replan_mode:
            self.controller.toggle_plan_details()
        elif not self._replan_mode:
            if not self.controller.toggle_diagram_source():
                self.controller.toggle_latest_trace()

    def action_trace_group(self) -> None:
        if not self._has_pending_plan() and not self._replan_mode:
            self.controller.toggle_trace_group()

    def action_plan_replan(self) -> None:
        if self._replan_mode:
            return
        if not self._has_pending_plan():
            self.controller.refresh_diagrams()
            return
        self._replan_mode = True
        composer = self.query_one("#composer", Composer)
        composer.placeholder = "输入重新规划要求，Enter 提交"
        composer.focus()
        note = self.query_one("#notification", Static)
        note.update("请输入重新规划要求，Enter 提交；Esc 取消计划")
        note.display = True

    async def action_escape(self) -> None:
        """Give Escape a useful, state-aware meaning in every main-screen state."""
        if self._has_pending_plan():
            self._replan_mode = False
            composer = self.query_one("#composer", Composer)
            composer.value = ""
            composer.placeholder = "输入任务或 /help …"
            await self.controller.review_plan(ReviewDecision(action="cancel"))
            return
        composer = self.query_one("#composer", Composer)
        if self._decision_mode == "approval_edit":
            # Esc 取消修改，回到等待决策，不改变审批结果。
            self._decision_mode = ""
            self._modified_args = None
            composer.value = ""
            self._render_state_immediately(self.controller.snapshot())
            return
        state = self.controller.state
        if state.pending_approval is not None:
            # fail closed：Esc（含修改后二次确认）视为拒绝当前工具调用。
            composer.value = ""
            self.handle_inline_approval("reject")
            return
        if state.pending_confirmation is not None:
            # 确认类操作按未确认处理，不执行写入或清空。
            composer.value = ""
            self.handle_inline_confirmation(False)
            return
        if self.controller.busy:
            await self.controller.cancel()
            return
        composer.value = ""

    def action_toggle_inspector(self) -> None:
        inspector = self.query_one("#inspector", InspectorPanel)
        inspector.display = not inspector.display

    def action_inspector_session(self) -> None:
        self.controller.set_inspector_view("session")

    def action_inspector_plan(self) -> None:
        self.controller.set_inspector_view("plan")

    def action_inspector_memory(self) -> None:
        self.controller.set_inspector_view("memory")

    def action_inspector_safety(self) -> None:
        self.controller.set_inspector_view("safety")

    def action_inspector_next(self) -> None:
        self.controller.cycle_inspector_view(1)

    def action_inspector_previous(self) -> None:
        self.controller.cycle_inspector_view(-1)

    def action_show_help(self) -> None:
        self._state = TuiState(
            **{**self._state.__dict__, "notification": "Enter 发送 · Ctrl+C 取消 · /plan 计划 · /model 切换模型 · /mcp 外部能力", "notification_level": "info"}
        )
        self._render_state_immediately(self._state)

    async def on_unmount(self) -> None:
        if self._render_timer is not None:
            self._render_timer.stop()
            self._render_timer = None
        self._pending_render_state = None
        await self.controller.shutdown()

    def on_resize(self, event: Resize) -> None:
        # Sidebar is useful on wide terminals and should never squeeze the
        # conversation below a usable width.
        self.query_one("#inspector", InspectorPanel).display = event.size.width >= 100
        # DiagramCard is a width-aware Rich renderable. Rewriting the log is
        # enough to relayout it; no model or LLM request is involved.
        self._render_state_immediately(self._state)


def run_tui(agent, settings: Settings, manager: ConfigManager) -> None:
    XgTuiApp(agent, settings, manager).run()
