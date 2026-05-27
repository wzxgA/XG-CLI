"""Provider 注册表单元测试。"""

from __future__ import annotations

from xg.config.providers import BUILTIN_PROVIDERS, Provider, ProviderRegistry


class TestBuiltin:
    def test_four_builtin_providers(self):
        assert {p.name for p in BUILTIN_PROVIDERS} == {"openai", "deepseek", "glm", "kimi"}

    def test_required_fields_present(self):
        for p in BUILTIN_PROVIDERS:
            assert p.api_base
            assert p.api_key_env
            assert p.default_model
            assert p.context_window > 0

    def test_capability_flags(self):
        openai = next(p for p in BUILTIN_PROVIDERS if p.name == "openai")
        deepseek = next(p for p in BUILTIN_PROVIDERS if p.name == "deepseek")
        assert openai.supports_cache and openai.supports_vision
        assert not deepseek.supports_cache and not deepseek.supports_vision


class TestRegistry:
    def test_get_known(self):
        assert ProviderRegistry().get("deepseek").default_model == "deepseek-chat"

    def test_get_unknown_returns_none(self):
        assert ProviderRegistry().get("nope") is None

    def test_register_custom(self):
        registry = ProviderRegistry()
        registry.register(
            Provider("custom", "Custom", "https://x/v1", "XG_CUSTOM_API_KEY", "m", 64000)
        )
        assert "custom" in registry.names()
        assert registry.get("custom").api_base == "https://x/v1"
