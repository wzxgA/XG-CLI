"""SmartRouter 档位表单 Modal（TUI）。

仅于 TUI 运行时 import。选择档位 + 填 provider；model 可选。
provider 留空 = 清空该档位（回落到手动 active）。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from xg.config.provider_service import validate_name
from xg.config.smart_router_service import SmartRouterConfigService

_TIER_ORDER = ("Basic", "Enhanced", "Superior", "Ultimate")
_TIER_LABELS = {
    "Basic": "Basic（闲聊/简单问答）",
    "Enhanced": "Enhanced（写函数/改文件）",
    "Superior": "Superior（重构/排查/多文件）",
    "Ultimate": "Ultimate（架构/设计/高风险）",
}


class SmartRouterForm(ModalScreen[None]):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, service: SmartRouterConfigService, *, tier: str | None = None) -> None:
        super().__init__()
        self.service = service
        self.tier = tier

    def compose(self) -> ComposeResult:
        with Vertical(id="tier-form"):
            yield Static("SmartRouter 档位", id="tier-form-title")
            yield Static("档位", id="t-tier-label")
            yield Select(
                ((_TIER_LABELS.get(t), t) for t in _TIER_ORDER),
                value=self.tier or "Basic",
                id="tier",
            )
            yield Static("provider（留空 = 清空该档位）", id="t-provider-label")
            yield Input(value="", id="provider", placeholder="如 deepseek")
            yield Static("model（可选，缺省取 default_model）", id="t-model-label")
            yield Input(value="", id="model", placeholder="如 deepseek-chat")
            with Horizontal(id="tier-form-actions"):
                yield Button("保存", id="save", variant="primary")
                yield Button("取消", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id in ("save", ""):
            self._save()

    def _save(self) -> None:
        tier = str(self.query_one("#tier", Select).value or "Basic")
        provider = self.query_one("#provider", Input).value.strip()
        model = self.query_one("#model", Input).value.strip() or None
        if not provider:
            result = self.service.clear_tier(tier)
        else:
            err = validate_name(provider)
            if err:
                return self.notify(f"provider 名非法: {err}", severity="error")
            result = self.service.set_tier(tier, provider, model)
        self.notify(result.message, severity="error" if not result.ok else "information")
        if result.ok:
            self.dismiss(None)