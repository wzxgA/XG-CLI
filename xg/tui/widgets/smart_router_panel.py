"""SmartRouter 配置面板（TUI，独立 ModalScreen，ctrlt 打开）。

仅于 TUI 运行时 import。展示开关与四档 provider/model，编辑走
:mod:`smart_router_form` 的 Modal；动作落 :class:`SmartRouterConfigService`。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from xg.config.smart_router_service import SmartRouterConfigService
from xg.tui.widgets.smart_router_form import SmartRouterForm


class SmartRouterScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "关闭")]

    def __init__(self, manager) -> None:
        super().__init__()
        self.service = SmartRouterConfigService(manager)

    def compose(self) -> ComposeResult:
        with Vertical(id="smart-router-dialog"):
            yield Static("SmartRouter 配置（输入框也可用 /tier 命令）", id="sr-title")
            yield Static(self._render_state(), id="sr-body")
            with Horizontal(id="sr-actions"):
                yield Button("开关", id="toggle")
                yield Button("编辑档位", id="edit", variant="primary")
                yield Button("刷新", id="refresh")
                yield Button("关闭", id="close")

    def _render_state(self) -> str:
        cfg = self.service.get()
        enabled = bool(cfg.get("enabled", False))
        rows = self.service.list_tiers()
        lines = [f"SmartRouter: {'开启' if enabled else '关闭'}",
                 "TIER       PROVIDER       MODEL"]
        for row in rows:
            if row["configured"]:
                lines.append(f"{row['name']:<10}{row['provider']:<14}{row['model']}")
            else:
                lines.append(f"{row['name']:<10}{'（回落 active）':<14}-")
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#sr-body", Static).update(self._render_state())

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "close":
            self.dismiss(None)
            return
        if bid == "refresh":
            self._refresh()
            return
        if bid == "toggle":
            cfg = self.service.get()
            on = not bool(cfg.get("enabled", False))
            self.notify(self.service.set_enabled(on).message)
            self._refresh()
            return
        if bid == "edit":
            return self.app.push_screen(
                SmartRouterForm(self.service), callback=self._after_form
            )

    def _after_form(self, _result: object) -> None:
        self._refresh()


def open_smart_router_screen(app, manager) -> None:
    """外部调用：打开 SmartRouter 面板。"""
    app.push_screen(SmartRouterScreen(manager))