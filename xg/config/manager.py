"""配置合并与持久化。

优先级（从低到高）：默认值 < 用户级 ~/.xg/config.json < 项目级 .xg/config.json < 环境变量/.env。
API Key 只从环境变量 / .env 读取，绝不写入配置文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from xg.config.providers import Provider, ProviderRegistry

USER_CONFIG = "config.json"
DEFAULT_CONTEXT_WINDOW = 128_000


@dataclass
class ActiveConfig:
    """解析后的当前生效配置快照。"""

    provider_name: str
    model: str
    api_base: str
    api_key: str
    context_window: int
    supports_cache: bool = False
    supports_vision: bool = False


def mask_key(key: str) -> str:
    """API Key 脱敏：前 4 位 + ****。"""
    if not key:
        return "(未配置)"
    return f"{key[:4]}****" if len(key) > 4 else "****"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个 dict，override 按名覆盖 base。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_env_file() -> Path | None:
    """从当前工作目录向上查找 .env（最多 3 层）。"""
    cur = Path.cwd()
    for _ in range(3):
        candidate = cur / ".env"
        if candidate.is_file():
            return candidate
        if cur == cur.parent:
            break
        cur = cur.parent
    return None


class ConfigManager:
    def __init__(
        self,
        user_dir: str | Path | None = None,
        project_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
        env_file: str | Path | None = None,
        load_env: bool = True,
    ) -> None:
        self.user_dir = Path(user_dir) if user_dir else Path.home() / ".xg"
        self.project_dir = Path(project_dir) if project_dir else Path.cwd() / ".xg"
        self.user_config_path = self.user_dir / USER_CONFIG
        self.project_config_path = self.project_dir / USER_CONFIG
        self.env: dict[str, str] = env if env is not None else os.environ
        self.registry = ProviderRegistry()
        if load_env:
            self._load_env_file(env_file)

    # ---------- 文件读写 ----------

    def _load_env_file(self, env_file: str | Path | None) -> None:
        if env_file is None:
            env_file = _find_env_file()
        if env_file and Path(env_file).is_file():
            load_dotenv(env_file, override=False)

    def _read_config(self, path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_config(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _merged_config(self) -> dict:
        """项目级按名覆盖用户级。"""
        return _deep_merge(
            self._read_config(self.user_config_path),
            self._read_config(self.project_config_path),
        )

    # ---------- provider 解析 ----------

    def provider_names(self) -> list[str]:
        """内置 + 配置文件中定义的自定义 provider。"""
        names = set(self.registry.names())
        names.update((self._merged_config().get("providers") or {}).keys())
        return sorted(names)

    def resolve_provider(self, name: str) -> Provider | None:
        """返回合并配置后的 provider；未定义返回 None。"""
        cfg = (self._merged_config().get("providers") or {}).get(name)
        if not isinstance(cfg, dict):
            cfg = None
        base = self.registry.get(name)

        if base is None and cfg is None:
            return None

        if base is None:
            required = ("api_base", "api_key_env", "default_model")
            if not cfg or not all(cfg.get(k) for k in required):
                return None
            return Provider(
                name=name,
                display_name=str(cfg.get("display_name", name)),
                api_base=str(cfg["api_base"]),
                api_key_env=str(cfg["api_key_env"]),
                default_model=str(cfg["default_model"]),
                context_window=int(cfg.get("context_window", DEFAULT_CONTEXT_WINDOW)),
                supports_cache=bool(cfg.get("supports_cache", False)),
                supports_vision=bool(cfg.get("supports_vision", False)),
            )

        return Provider(
            name=base.name,
            display_name=str((cfg or {}).get("display_name", base.display_name)),
            api_base=str((cfg or {}).get("api_base", base.api_base)),
            api_key_env=str((cfg or {}).get("api_key_env", base.api_key_env)),
            default_model=str((cfg or {}).get("default_model", base.default_model)),
            context_window=int((cfg or {}).get("context_window", base.context_window)),
            supports_cache=base.supports_cache,
            supports_vision=base.supports_vision,
        )

    def list_providers(self) -> list[Provider]:
        return [p for name in self.provider_names() if (p := self.resolve_provider(name))]

    def resolve_api_key(self, provider: Provider) -> str:
        """API Key 读取链：provider.api_key_env → （仅隐式 openai）兼容旧键 XG_API_KEY。

        避免把 XG_API_KEY（如 OpenAI 的 key）误发给其他 provider。
        """
        value = self.env.get(provider.api_key_env)
        if value:
            return value
        if provider.name == "openai":
            return self.env.get("XG_API_KEY", "")
        return ""

    def resolve_window(self, provider: Provider) -> int:
        """上下文窗口：XG_CONTEXT_WINDOW 环境变量 > provider 能力。"""
        raw = self.env.get("XG_CONTEXT_WINDOW", "")
        try:
            return int(raw) if raw else provider.context_window
        except ValueError:
            return provider.context_window

    # ---------- 生效配置 ----------

    def active(self) -> ActiveConfig:
        merged = self._merged_config()
        provider_name = str(merged.get("active_provider", "") or "openai")
        provider = self.resolve_provider(provider_name) or self.registry.get("openai")
        if provider is None:
            provider = self.registry.all()[0]
            provider_name = provider.name

        # 模型：配置 active_model > 环境变量 XG_MODEL > provider 默认
        model = (
            str(merged.get("active_model", "") or "")
            or self.env.get("XG_MODEL", "")
            or provider.default_model
        )
        # base url：仅隐式 provider（openai）允许 XG_API_BASE 覆盖，避免误伤其他 provider
        api_base = provider.api_base
        if provider.name == "openai" and self.env.get("XG_API_BASE"):
            api_base = self.env["XG_API_BASE"]

        return ActiveConfig(
            provider_name=provider.name,
            model=model,
            api_base=api_base,
            api_key=self.resolve_api_key(provider),
            context_window=self.resolve_window(provider),
            supports_cache=provider.supports_cache,
            supports_vision=provider.supports_vision,
        )

    # ---------- 持久化 ----------

    def set_active(self, provider: str, model: str) -> None:
        user = self._read_config(self.user_config_path)
        user["active_provider"] = provider
        user["active_model"] = model
        self._write_config(self.user_config_path, user)

    def set_config_value(self, dotted_key: str, value: str) -> None:
        """按点路径写入用户配置，如 providers.deepseek.default_model。"""
        user = self._read_config(self.user_config_path)
        keys = dotted_key.split(".")
        node: Any = user
        for key in keys[:-1]:
            nxt = node.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                node[key] = nxt
            node = nxt
        node[keys[-1]] = value
        self._write_config(self.user_config_path, user)

    def get_config_value(self, dotted_key: str) -> str | None:
        """从合并配置读取值；非标量时返回 JSON 字符串。"""
        node: Any = self._merged_config()
        for key in dotted_key.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
        if isinstance(node, (str, int, float, bool)):
            return str(node)
        return json.dumps(node, ensure_ascii=False)
