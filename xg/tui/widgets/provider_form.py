"""Provider 新增/编辑/写 Key 表单 Modal（TUI）。

仅于 TUI 运行时 import。字段含即时校验；API Key 为密码型输入，
改写前经 :meth:`ProviderConfigService.set_api_key` 的覆盖确认与脱敏。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from xg.config.provider_service import ProviderConfigService, validate_api_base, validate_name


class ProviderForm(ModalScreen[None]):
    """mode: ``add`` / ``edit`` / ``key``。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, service: ProviderConfigService, *, mode: str, name: str | None = None) -> None:
        super().__init__()
        self.service = service
        self.mode = mode
        # 用独立属性承载目标 provider 名，避开 ModalScreen.name 只读属性
        self._target = name
        self._row = service.get(name) if name else None

    def compose(self) -> ComposeResult:
        title = {"add": "新增 Provider", "edit": "编辑 Provider", "key": "写入 API Key"}[self.mode]
        with Vertical(id="provider-form"):
            yield Static(title, id="provider-form-title")
            if self.mode in ("add", "edit"):
                yield Static("名称", id="f-name-label")
                yield Input(
                    value=self._target or "", id="name",
                    placeholder="仅字母数字下划线连字符，不含空格",
                    disabled=self.mode == "edit",
                )
                if self.mode == "add":
                    yield Static("api_base", id="f-base-label")
                    yield Input(value=(self._row or {}).get("api_base", ""), id="api_base",
                                placeholder="https://gateway.my.com/v1")
                    yield Static("default_model", id="f-model-label")
                    yield Input(value=(self._row or {}).get("default_model", ""), id="default_model")
                    yield Static("display_name（可选）", id="f-label-label")
                    yield Input(value=(self._row or {}).get("display_name", ""), id="display_name")
            elif self.mode == "edit":
                yield Static("api_base", id="f-base-label")
                yield Input(value=(self._row or {}).get("api_base", ""), id="api_base")
                yield Static("display_name（可选）", id="f-label-label")
                yield Input(value=(self._row or {}).get("display_name", ""), id="display_name")
            elif self.mode == "key":
                yield Static(f"API Key（{self._target}）", id="f-key-label")
                yield Input(value="", id="api_key", password=True, placeholder=f"XG_{self._target.upper()}_API_KEY")
            with Horizontal(id="provider-form-actions"):
                yield Button("保存", id="save", variant="primary")
                yield Button("取消", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "save":
            self._save()
            return
        # Enter 在 Input 上触发同一保存逻辑
        if event.button.id == "" and self.mode in ("add", "edit", "key"):
            self._save()

    def _save(self) -> None:
        if self.mode == "add":
            name = self.query_one("#name", Input).value.strip()
            base = self.query_one("#api_base", Input).value.strip()
            model = self.query_one("#default_model", Input).value.strip()
            label = self.query_one("#display_name", Input).value.strip() or None
            err = validate_name(name) or validate_api_base(base)
            if err:
                return self.notify(err, severity="error")
            result = self.service.add(name, base, model, display_name=label)
        elif self.mode == "edit":
            base = self.query_one("#api_base", Input).value.strip()
            label = self.query_one("#display_name", Input).value.strip() or None
            err = validate_api_base(base)
            if err:
                return self.notify(err, severity="error")
            result = self.service.update(self._target, {"api_base": base, "display_name": label})
        else:  # key
            key = self.query_one("#api_key", Input).value.strip()
            result = self.service.set_api_key(self._target, key, overwrite=True, yes=True)
        self.notify(result.message, severity="error" if not result.ok else "information")
        if result.ok:
            self.dismiss(None)