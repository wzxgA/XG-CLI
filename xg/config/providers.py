"""Provider 数据模型与注册表。

内置 GLM / DeepSeek / Kimi / OpenAI 兼容四类 provider。
能力参数（window / cache / vision）为起始预设值，需与官方文档核对后固化。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str                 # openai / deepseek / glm / kimi ...
    display_name: str
    api_base: str             # 默认 base url（可被配置/环境变量覆盖）
    api_key_env: str          # API Key 对应的环境变量名，如 XG_DEEPSEEK_API_KEY
    default_model: str
    context_window: int       # 上下文窗口（token），用于预算控制
    supports_cache: bool = False   # 是否支持 prompt cache（预留）
    supports_vision: bool = False  # 是否支持图片输入（预留）


BUILTIN_PROVIDERS: list[Provider] = [
    Provider(
        name="openai",
        display_name="OpenAI",
        api_base="https://api.openai.com/v1",
        api_key_env="XG_OPENAI_API_KEY",
        default_model="gpt-4o-mini",
        context_window=128_000,
        supports_cache=True,
        supports_vision=True,
    ),
    Provider(
        name="deepseek",
        display_name="DeepSeek",
        api_base="https://api.deepseek.com/v1",
        api_key_env="XG_DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        context_window=128_000,
    ),
    Provider(
        name="glm",
        display_name="GLM",
        api_base="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="XG_GLM_API_KEY",
        default_model="glm-4-flash",
        context_window=128_000,
        supports_cache=True,
        supports_vision=True,
    ),
    Provider(
        name="kimi",
        display_name="Kimi",
        api_base="https://api.moonshot.cn/v1",
        api_key_env="XG_KIMI_API_KEY",
        default_model="moonshot-v1-8k",
        context_window=8_000,
    ),
]


class ProviderRegistry:
    """内置 + 用户自定义 provider 的注册表。"""

    def __init__(self, providers: list[Provider] | None = None) -> None:
        self._providers: dict[str, Provider] = {
            p.name: p for p in (providers if providers is not None else BUILTIN_PROVIDERS)
        }

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def names(self) -> list[str]:
        return list(self._providers)

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider
