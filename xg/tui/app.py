"""Textual fullscreen application for XG."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.widgets import Static

from xg.agent.plan import ReviewDecision
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.safety.hitl import ApprovalDecision
from xg.tui.controller import SessionController
from xg.tui.messages import StateChanged
from xg.tui.state import TuiState
from xg.tui.widgets.approval_modal import ApprovalModal
from xg.tui.widgets.composer import Composer
from xg.tui.widgets.confirm_modal import ConfirmModal
from xg.tui.widgets.footer import FooterBar
from xg.tui.widgets.header import HeaderBar
from xg.tui.widgets.inspector import InspectorPanel
from xg.tui.widgets.plan_modal import PlanModal
from xg.tui.widgets.transcript import TranscriptView


class XgTuiApp(App[None]):
    """One active session, with all execution delegated to the controller."""

    TITLE = "XG"
    CSS_PATH = "theme.tcss"
    BINDINGS = [
        ("ctrl+c", "cancel_turn", "取消当前任务"),
        ("ctrl+l", "clear_transcript", "清屏"),
        ("ctrl+r", "toggle_inspector", "侧栏"),
        ("f1", "show_help", "帮助"),
    ]

    def __init__(self, agent, settings: Settings, manager: ConfigManager) -> None:
        super().__init__()
        self.controller = SessionController(agent, settings, manager, self._on_state_change)
        self._state = self.controller.snapshot()
        self._modal_kind = ""

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield TranscriptView(id="transcript")
            yield InspectorPanel(id="inspector")
        yield Static("", id="notification")
        yield Composer()
        yield FooterBar()

    def on_mount(self) -> None:
        self._render_state(self._state)
        self.query_one("#composer", Composer).focus()

    def _on_state_change(self, state: TuiState) -> None:
        self.post_message(StateChanged(state))

    def on_state_changed(self, message: StateChanged) -> None:
        self._state = message.state
        self._render_state(message.state)
        self._show_pending_modal(message.state)

    def _render_state(self, state: TuiState) -> None:
        self.query_one("#header", HeaderBar).update_state(state)
        self.query_one("#transcript", TranscriptView).update_state(state)
        self.query_one("#inspector", InspectorPanel).update_state(state)
        note = self.query_one("#notification", Static)
        note.update(state.notification)
        note.display = bool(state.notification)
        composer = self.query_one("#composer", Composer)
        composer.disabled = state.phase in ("running", "awaiting_approval", "awaiting_plan_review") or state.pending_confirmation is not None

    def _show_pending_modal(self, state: TuiState) -> None:
        if state.pending_approval is not None and self._modal_kind != "approval":
            self._modal_kind = "approval"
            self.push_screen(ApprovalModal(state.pending_approval), self._approval_closed)
        elif state.pending_plan is not None and self._modal_kind != "plan":
            self._modal_kind = "plan"
            self.push_screen(PlanModal(state.pending_plan), self._plan_closed)
        elif state.pending_confirmation is not None and self._modal_kind != "confirm":
            self._modal_kind = "confirm"
            self.push_screen(ConfirmModal(state.pending_confirmation), self._confirm_closed)

    def _approval_closed(self, value: str | None) -> None:
        self._modal_kind = ""
        decisions = {
            "approve": ApprovalDecision(allow=True, reason="user_approved"),
            "allow-all": ApprovalDecision(allow=True, reason="user_approved_allow_all"),
            "allow_all": ApprovalDecision(allow=True, reason="user_approved_allow_all"),
            "skip": ApprovalDecision(allow=False, reason="user_skipped"),
        }
        if value and value.startswith("modify:"):
            try:
                import json
                decision = ApprovalDecision(allow=True, args=json.loads(value[7:]), reason="user_modified")
            except (ValueError, TypeError, json.JSONDecodeError):
                decision = ApprovalDecision(allow=False, reason="invalid_modified_args")
        else:
            decision = decisions.get(value or "reject", ApprovalDecision(allow=False, reason="user_rejected"))
        asyncio.create_task(self.controller.approve_tool(decision))

    def _plan_closed(self, value: str | None) -> None:
        self._modal_kind = ""
        if value == "execute":
            decision = ReviewDecision(action="execute")
        elif value and value.startswith("replan:"):
            decision = ReviewDecision(action="replan", feedback=value[7:])
        else:
            decision = ReviewDecision(action="cancel")
        asyncio.create_task(self.controller.review_plan(decision))

    def _confirm_closed(self, value: str | None) -> None:
        self._modal_kind = ""
        asyncio.create_task(self.controller.confirm_command(value == "confirm"))

    def on_input_submitted(self, event: Composer.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if text:
            asyncio.create_task(self._submit_text(text))

    async def _submit_text(self, text: str) -> None:
        try:
            await self.controller.submit(text)
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
        await self.controller.execute_command("/clear")
        self._state = self.controller.snapshot()
        self._render_state(self._state)

    def action_toggle_inspector(self) -> None:
        inspector = self.query_one("#inspector", InspectorPanel)
        inspector.display = not inspector.display

    def action_show_help(self) -> None:
        self._state = TuiState(
            **{**self._state.__dict__, "notification": "Enter 发送 · Ctrl+C 取消 · /plan 计划 · /model 切换模型 · /memory 管理记忆", "notification_level": "info"}
        )
        self._render_state(self._state)

    async def on_unmount(self) -> None:
        await self.controller.shutdown()

    def on_resize(self, event: Resize) -> None:
        # Sidebar is useful on wide terminals and should never squeeze the
        # conversation below a usable width.
        self.query_one("#inspector", InspectorPanel).display = event.size.width >= 100


def run_tui(agent, settings: Settings, manager: ConfigManager) -> None:
    XgTuiApp(agent, settings, manager).run()
