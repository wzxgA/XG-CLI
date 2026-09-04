"""Provider 配置服务：inline 斜杠命令与 TUI 面板共享的 CRUD 入口。

设计口径：
- config.json 是 provider 定义与 API Key 的唯一存储，不再写 .env。
- 写 config.json 复用 :class:`xg.config.manager.ConfigManager` 的分层读写。
- 所有写入在保存前经过校验（api_base 合法、default_model 必填、name 合法、
  key 非占位值），并把错误前移到输入阶段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from xg.config.manager import ConfigManager, _is_placeholder, mask_key

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_ALLOWED_FIELDS = ("api_base", "default_model", "display_name")


@dataclass
class OpResult:
    ok: bool
    message: str
    data: object | None = None


def validate_api_base(url: str) -> str | None:
    """校验 api_base；非法时返回错误提示，合法返回 None（对齐反引号坑 J6）。"""
    if not url or url != url.strip():
        return "api_base 不能为空或包含首尾空白"
    if any(ch in url for ch in ("`", " ")):
        return "api_base 含非法字符（如反引号或空格），请去掉后重试"
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        return f"api_base 无法解析为 URL: {exc}"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "api_base 必须是合法的 http(s) URL（如 https://gateway.my.com/v1）"
    return None


def validate_name(name: str) -> str | None:
    """校验 provider 名称；非法时返回提示。"""
    if not name:
        return "provider 名称不能为空"
    if not _NAME_RE.fullmatch(name):
        return "provider 名称只能包含字母、数字、下划线或连字符，且不含空格"
    return None


class ProviderConfigService:
    """面向界面的 provider 管理服务（无内置预设，纯用户自定义）。"""

    def __init__(self, manager: ConfigManager, settings: object | None = None) -> None:
        self.manager = manager
        self.settings = settings

    # ---------- 读取 ----------

    def _active_provider(self) -> str:
        if self.settings is not None and getattr(self.settings, "provider", ""):
            return str(self.settings.provider)
        try:
            return self.manager.active().provider_name
        except Exception:
            return ""

    def list(self) -> list[dict]:
        """返回每个 provider 的视图：脱敏、来源层、是否 base、key 是否已配置。"""
        active = self._active_provider()
        rows = []
        for name in self.manager.provider_names():
            provider = self.manager.resolve_provider(name)
            if provider is None:
                continue
            raw = provider.api_key
            configured = bool(raw) and not _is_placeholder(raw)
            rows.append(
                {
                    "name": name,
                    "display_name": provider.display_name,
                    "api_base": provider.api_base,
                    "default_model": provider.default_model,
                    "has_key": configured,
                    "is_base": name == active,
                    "layer": self.manager.provider_layer(name) or "user",
                }
            )
        return rows

    def get(self, name: str) -> dict | None:
        provider = self.manager.resolve_provider(name)
        if provider is None:
            return None
        raw = provider.api_key
        configured = bool(raw) and not _is_placeholder(raw)
        return {
            "name": name,
            "display_name": provider.display_name,
            "api_base": provider.api_base,
            "default_model": provider.default_model,
            "api_key": raw if configured else "",
            "api_key_masked": mask_key(raw if configured else ""),
            "has_key": configured,
            "is_base": name == self._active_provider(),
            "layer": self.manager.provider_layer(name) or "user",
        }

    def referenced_by(self, name: str) -> list[str]:
        """返回引用该 provider 的 SmartRouter 档位名（缺省空）。"""
        tiers = (self.manager.smart_router_config().get("tiers") or {})
        return [t for t, cfg in tiers.items() if cfg.get("provider") == name]

    # ---------- 写入 ----------

    def add(
        self,
        name: str,
        api_base: str,
        default_model: str,
        display_name: str | None = None,
        api_key: str | None = None,
    ) -> OpResult:
        if self.manager.resolve_provider(name) is not None:
            return OpResult(False, f"provider 已存在: {name}，如需修改请用 update/set（J4）")
        err = self._validate_fields(api_base, default_model)
        if err:
            return OpResult(False, err)
        fields = {
            "api_base": api_base.strip(),
            "default_model": default_model.strip(),
            "display_name": display_name.strip() if display_name else None,
        }
        layer = self.manager.upsert_provider(name, fields)
        out = f"已添加 provider: {name}（写入 {layer} 配置）"
        if api_key:
            key_result = self.set_api_key(name, api_key)
            if not key_result.ok:
                out += f"{key_result.message}"
        return OpResult(True, out, {"layer": layer})

    def update(self, name: str, fields: dict) -> OpResult:
        provider = self.manager.resolve_provider(name)
        if provider is None:
            return OpResult(False, f"未知 provider: {name}，可用: {', '.join(self.manager.provider_names()) or '（无）'}")
        unknown = [k for k in fields if k not in _ALLOWED_FIELDS]
        if unknown:
            return OpResult(False, f"不支持字段: {', '.join(unknown)}；可用: {', '.join(_ALLOWED_FIELDS)}")
        api_base = fields.get("api_base")
        if api_base is not None:
            err = validate_api_base(str(api_base))
            if err:
                return OpResult(False, err)
        default_model = fields.get("default_model")
        if default_model is not None and not str(default_model).strip():
            return OpResult(False, "default_model 必填，不能为空（J7）")
        normalized = {}
        if api_base is not None:
            normalized["api_base"] = str(api_base).strip()
        if default_model is not None:
            normalized["default_model"] = str(default_model).strip()
        if "display_name" in fields:
            normalized["display_name"] = (
                str(fields["display_name"]).strip() if fields["display_name"] or fields["display_name"] == "" else None
            )
        layer = self.manager.upsert_provider(name, normalized)
        return OpResult(True, f"已更新 provider: {name}（写入 {layer} 配置）")

    def remove(self, name: str, *, yes: bool = False) -> OpResult:
        provider = self.manager.resolve_provider(name)
        if provider is None:
            return OpResult(False, f"未知 provider: {name}")
        referenced = self.referenced_by(name)
        warnings = []
        if referenced:
            warnings.append(f"被 SmartRouter 档位引用：{', '.join(referenced)}（删除后这些档位会回落 base）")
        if name == self._active_provider():
            warnings.append("这是当前 base provider，删除后将没有可用 base")
        if not yes:
            head = "；".join(warnings) + "。" if warnings else "此操作会删除该 provider。"
            return OpResult(False, f"{head} 确认请加 --yes（/provider remove {name} --yes）")
        self.manager.delete_provider(name)
        msg = f"已删除 provider: {name}"
        if warnings:
            msg += "（" + "；".join(warnings) + "）"
        return OpResult(True, msg)

    def switch(self, name: str, model: str | None = None) -> OpResult:
        provider = self.manager.resolve_provider(name)
        if provider is None:
            return OpResult(False, f"未知 provider: {name}，可用: {', '.join(self.manager.provider_names()) or '（无）'}")
        target_model = (model or "").strip() or provider.default_model
        self.manager.set_active(provider.name, target_model)
        return OpResult(True, f"base 已切换: {provider.name} / {target_model}")

    def set_api_key(self, name: str, key: str, *, overwrite: bool = False, yes: bool = False) -> OpResult:
        provider = self.manager.resolve_provider(name)
        if provider is None:
            return OpResult(False, f"未知 provider: {name}")
        if _is_placeholder(key):
            return OpResult(False, "占位值（如 sk-xxx）视为未配置，已拒绝写入")
        existing = provider.api_key
        if existing and not _is_placeholder(existing) and not overwrite:
            if not yes:
                return OpResult(
                    False,
                    f"{name} 已配置 key {mask_key(existing)}，覆盖请加 --yes（/provider key {name} <KEY> --yes）",
                )
            overwrite = True
        self.manager.upsert_provider(name, {"api_key": key})
        status = "已写入" if (existing and existing != key) or not existing else "未改动"
        extra = f"（覆盖旧值 {mask_key(existing)}）" if existing and existing != key else ""
        return OpResult(True, f"{status} providers.{name}.api_key -> config.json，仅显示 {mask_key(key)}{extra}")

    # ---------- 校验 ----------

    def _validate_fields(self, api_base: str, default_model: str) -> str | None:
        err = validate_api_base(str(api_base))
        if err:
            return err
        if not str(default_model).strip():
            return "default_model 必填，不能为空（J7）"
        return None