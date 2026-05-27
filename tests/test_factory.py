"""LLM 工厂单元测试。"""

from __future__ import annotations

from xg.config.providers import Provider
from xg.llm.factory import create_client
from xg.llm.openai_compat import OpenAICompatClient


def test_create_client_passthrough():
    provider = Provider(
        name="deepseek",
        display_name="DeepSeek",
        api_base="https://api.deepseek.com/v1",
        api_key_env="XG_DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        context_window=128_000,
    )
    client = create_client(provider, "sk-123", "deepseek-reasoner")
    assert isinstance(client, OpenAICompatClient)
    assert client.api_base == "https://api.deepseek.com/v1"
    assert client.api_key == "sk-123"
    assert client.model == "deepseek-reasoner"
