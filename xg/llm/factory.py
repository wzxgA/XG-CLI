"""LlmClient 工厂：按 provider 配置构建客户端。

本期所有 provider 共用 OpenAI-compatible 实现，工厂是后续 provider
差异化（能力、协议）的扩展点。
"""

from __future__ import annotations

from xg.config.providers import Provider
from xg.llm.client import LlmClient
from xg.llm.openai_compat import OpenAICompatClient

DEFAULT_TIMEOUT = 120.0


def create_client(
    provider: Provider,
    api_key: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> LlmClient:
    return OpenAICompatClient(
        api_base=provider.api_base,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
