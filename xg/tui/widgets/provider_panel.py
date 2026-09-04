"""Provider 管理面板（TUI）。

仅于 TUI 运行时 import（与 ``confirm_modal`` 一致）；无 textual 的测试环境会跳过。
面板只读展示 provider 列表（脱敏、标注 base 与来源层），新增/编辑/写Key 由
:mod:`provider_form` 的表单 Modal 完成；所有动作落到
:class:`xg.config.provider_service.ProviderConfigService`。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from xg.config.provider_service import ProviderConfigService
from xg.tui.widgets.provider_form import ProviderForm


class ProviderScreen(ModalScreen[None]):
    """Provider 列表；Esc 关闭，按钮进入表单 / 删除 / 写 Key。"""

    BINDINGS = [("escape", "close", "关闭")]

    def __init__(self, manager, settings) -> None:
        super().__init__()
        self.service = ProviderConfigService(manager, settings)
        self._rows: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-dialog"):
            yield Static("Provider 管理（在输入框也可用 /provider 命令）", id="provider-title")
            yield Static(self._render_list(), id="provider-list")
            with Horizontal(id="provider-actions"):
                yield Button("新增", id="add", variant="primary")
                yield Button("编辑", id="edit")
                yield Button("设为base", id="switch")
                yield Button("删除", id="remove", variant="error")
                yield Button("写Key", id="key")
                yield Button("刷新", id="refresh")
                yield Button("关闭", id="close")

    def _render_list(self) -> str:
        self._rows = self.service.list()
        if not self._rows:
            return "尚未配置任何 provider。请用 [新增]，或直接输入:\n/provider add <name> <api_base> --model <M> --key K"
        lines = ["NAME              DISPLAY   BASE_URL                             MODEL            KEY  BASE  LAYER"]
        for row in self._rows:
            lines.append(
                f"{row['name']:<18}"
                f"{row['display_name'][:8]:<8}"
                f"{row['api_base'][:34]:<34}"
                f"{row['default_model'][:16]:<16}"
                f"{'✓' if row['has_key'] else '✗':^5}"
                f"{'●' if row['is_base'] else ' ':^6}"
                f"{row['layer']}"
            )
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#provider-list", Static).update(self._render_list())

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
        if bid == "add":
            return self.app.push_screen(
                ProviderForm(self.service, mode="add"), callback=self._after_form
            )
        if not self._rows:
            self.notify("请先新增 provider", severity="warning")
            return
        if bid == "edit":
            return self.app.push_screen(
                ProviderForm(self.service, name=self._rows[0]["name"], mode="edit"),
                callback=self._after_form,
            )
        if bid == "switch":
            self.notify(self.service.switch(self._rows[0]["name"]).message)
        if bid == "remove":
            result = self.service.remove(self._rows[0]["name"], yes=True)
            self.notify(result.message, severity="error" if not result.ok else "information")
            self._refresh()
        if bid == "key":
            return self.app.push_screen(
                ProviderForm(self.service, name=self._rows[0]["name"], mode="key"),
                callback=self._after_form,
            )

    def _after_form(self, _result: object) -> None:
        self._refresh()


def open_provider_screen(app, manager, settings) -> None:
    """外部调用：打开 provider 面板。"""
    app.push_screen(ProviderScreen(manager, settings))