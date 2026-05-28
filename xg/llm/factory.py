"""LlmClient 工厂：按解析后的 api_base / key / model 组装客户端。

URL 与 Key 的解析（环境变量、配置合并）由 ConfigManager 负责，
工厂只负责组装客户端，是后续 provider 差异化（能力、协议）的扩展点。
"""

from __future__ import annotations

from xg.llm.client import LlmClient
from xg.llm.openai_compat import OpenAICompatClient

DEFAULT_TIMEOUT = 120.0


def create_client(
    api_base: str,
    api_key: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> LlmClient:
    return OpenAICompatClient(
        api_base=api_base,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
