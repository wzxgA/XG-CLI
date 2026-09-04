"""Provider 数据模型与注册表。

provider 不再内置预设，全部由用户在配置（config.json 的 ``providers`` 节）
中自定义 name / api_base / default_model 等；API Key 经 ``XG_<NAME>_API_KEY``
环境变量提供。能力参数（window / cache / vision）为可选预设值。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str                 # 用户自定义名称
    display_name: str
    api_base: str             # 默认 base url（可被配置/环境变量覆盖）
    api_key_env: str          # API Key 对应的环境变量名，如 XG_MY_PROVIDER_API_KEY
    default_model: str
    context_window: int       # 上下文窗口（token），用于预算控制
    supports_cache: bool = False   # 是否支持 prompt cache（预留）
    supports_vision: bool = False  # 是否支持图片输入（预留）


class ProviderRegistry:
    """用户自定义 provider 的注册表（无内置预设，初始为空）。"""

    def __init__(self, providers: list[Provider] | None = None) -> None:
        self._providers: dict[str, Provider] = {
            p.name: p for p in (providers if providers is not None else [])
        }

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def names(self) -> list[str]:
        return list(self._providers)

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider
