"""ConfigManager 单元测试：三层合并、持久化、旧环境变量兼容。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xg.config.manager import ConfigManager, mask_key


def make_manager(
    tmp_path: Path,
    env: dict | None = None,
    user_cfg: dict | None = None,
    project_cfg: dict | None = None,
) -> ConfigManager:
    user_dir = tmp_path / "user_xg"
    project_dir = tmp_path / "proj_xg"
    user_dir.mkdir(exist_ok=True)
    project_dir.mkdir(exist_ok=True)
    if user_cfg is not None:
        (user_dir / "config.json").write_text(json.dumps(user_cfg), encoding="utf-8")
    if project_cfg is not None:
        (project_dir / "config.json").write_text(json.dumps(project_cfg), encoding="utf-8")
    return ConfigManager(
        user_dir=user_dir,
        project_dir=project_dir,
        env=dict(env or {}),
        load_env=False,
    )


class TestLegacyEnv:
    def test_implicit_openai_provider(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_API_BASE": "https://legacy.test/v1", "XG_OPENAI_API_KEY": "k-123", "XG_MODEL": "old-model"},
        )
        active = manager.active()
        assert active.provider_name == "openai"
        assert active.api_base == "https://legacy.test/v1"
        assert active.api_key == "k-123"
        assert active.model == "old-model"

    def test_empty_env_falls_back_to_defaults(self, tmp_path):
        manager = make_manager(tmp_path, env={})
        active = manager.active()
        assert active.provider_name == "openai"
        assert active.api_key == ""
        assert active.model == "gpt-4o-mini"
        assert active.api_base == "https://api.openai.com/v1"


class TestProviderKeyEnv:
    def test_provider_specific_key(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_DEEPSEEK_API_KEY": "dk-1"},
            user_cfg={"active_provider": "deepseek"},
        )
        active = manager.active()
        assert active.provider_name == "deepseek"
        assert active.api_key == "dk-1"
        assert active.api_base == "https://api.deepseek.com/v1"
        assert active.model == "deepseek-chat"

    def test_no_generic_fallback(self, tmp_path):
        """无通用兜底：provider 未配专属 Key 时即为空。"""
        manager = make_manager(
            tmp_path,
            env={"XG_API_KEY": "legacy-key"},
            user_cfg={"active_provider": "glm"},
        )
        assert manager.active().api_key == ""

    def test_openai_requires_specific_key(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_API_KEY": "legacy-key"})
        assert manager.active().api_key == ""


class TestPlaceholderKeys:
    """占位值（sk-xxx）应视为未配置，避免被当真实 key 使用（401 陷阱）。"""

    def test_placeholder_specific_means_no_key(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "deepseek", "XG_DEEPSEEK_API_KEY": "sk-xxx"},
        )
        assert manager.active().api_key == ""

    def test_real_specific_key_wins(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "deepseek", "XG_DEEPSEEK_API_KEY": "sk-real-dk"},
        )
        assert manager.active().api_key == "sk-real-dk"

    def test_missing_specific_means_no_key(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "deepseek", "XG_API_KEY": "sk-real"},
        )
        assert manager.active().api_key == ""


class TestEnvProviderSelection:
    def test_env_provider_wins(self, tmp_path):
        """XG_PROVIDER 环境变量选择激活 provider，优先于配置文件。"""
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "deepseek", "XG_DEEPSEEK_API_KEY": "dk"},
            user_cfg={"active_provider": "glm"},
        )
        active = manager.active()
        assert active.provider_name == "deepseek"
        assert active.api_base == "https://api.deepseek.com/v1"
        assert active.api_key == "dk"

    def test_env_provider_with_specific_key(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "glm", "XG_GLM_API_KEY": "gk"},
        )
        active = manager.active()
        assert active.provider_name == "glm"
        assert active.api_key == "gk"

    def test_env_provider_unknown_falls_back(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_PROVIDER": "nope", "XG_API_KEY": "k"})
        assert manager.active().provider_name == "openai"


class TestMergePriority:
    def test_user_config_active(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_DEEPSEEK_API_KEY": "dk"},
            user_cfg={"active_provider": "deepseek", "active_model": "deepseek-reasoner"},
        )
        active = manager.active()
        assert active.model == "deepseek-reasoner"

    def test_project_overrides_user(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_DEEPSEEK_API_KEY": "dk"},
            user_cfg={
                "active_provider": "deepseek",
                "providers": {"deepseek": {"api_base": "https://user.test/v1"}},
            },
            project_cfg={
                "providers": {"deepseek": {"api_base": "https://project.test/v1"}},
            },
        )
        assert manager.active().api_base == "https://project.test/v1"

    def test_env_overrides_project_for_openai(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_API_BASE": "https://env.test/v1", "XG_API_KEY": "k"},
            project_cfg={"providers": {"openai": {"api_base": "https://project.test/v1"}}},
        )
        assert manager.active().api_base == "https://env.test/v1"

    def test_legacy_base_does_not_leak_to_other_providers(self, tmp_path):
        """XG_API_BASE 只覆盖隐式 openai，不应覆盖已配置的 deepseek。"""
        manager = make_manager(
            tmp_path,
            env={"XG_API_BASE": "https://env.test/v1", "XG_DEEPSEEK_API_KEY": "dk"},
            user_cfg={"active_provider": "deepseek"},
        )
        assert manager.active().api_base == "https://api.deepseek.com/v1"


class TestPerProviderUrl:
    def test_deepseek_url_env_override(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "deepseek", "XG_DEEPSEEK_API_KEY": "dk",
                 "XG_DEEPSEEK_API_BASE": "https://my-deepseek-proxy.test/v1"},
        )
        assert manager.active().api_base == "https://my-deepseek-proxy.test/v1"

    def test_openai_specific_url_beats_legacy(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_API_KEY": "k", "XG_API_BASE": "https://legacy.test/v1",
                 "XG_OPENAI_API_BASE": "https://openai-proxy.test/v1"},
        )
        assert manager.active().api_base == "https://openai-proxy.test/v1"

    def test_url_env_wins_over_config_file(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "glm", "XG_GLM_API_KEY": "gk",
                 "XG_GLM_API_BASE": "https://env.test/v1"},
            user_cfg={"providers": {"glm": {"api_base": "https://config.test/v1"}}},
        )
        assert manager.active().api_base == "https://env.test/v1"

    def test_no_url_env_uses_preset(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "kimi", "XG_KIMI_API_KEY": "kk"},
        )
        assert manager.active().api_base == "https://api.moonshot.cn/v1"

    def test_window_env_override(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_CONTEXT_WINDOW": "16000", "XG_DEEPSEEK_API_KEY": "dk"},
            user_cfg={"active_provider": "deepseek"},
        )
        assert manager.active().context_window == 16000


class TestCustomProvider:
    def test_custom_provider_from_config(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_CUSTOM_API_KEY": "ck"},
            user_cfg={
                "active_provider": "myproxy",
                "providers": {
                    "myproxy": {
                        "api_base": "https://my-proxy.test/v1",
                        "api_key_env": "XG_CUSTOM_API_KEY",
                        "default_model": "my-model",
                        "context_window": 64000,
                    }
                },
            },
        )
        assert "myproxy" in manager.provider_names()
        active = manager.active()
        assert active.provider_name == "myproxy"
        assert active.api_base == "https://my-proxy.test/v1"
        assert active.api_key == "ck"
        assert active.context_window == 64000

    def test_custom_provider_missing_required_fields(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={"providers": {"broken": {"api_base": "https://x/v1"}}},
        )
        assert manager.resolve_provider("broken") is None


class TestPersistence:
    def test_set_active_persists_and_reloads(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_GLM_API_KEY": "gk"})
        manager.set_active("glm", "glm-4-plus")

        user_cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert user_cfg["active_provider"] == "glm"
        assert user_cfg["active_model"] == "glm-4-plus"

        # 新的 manager 实例应读到持久化结果
        reloaded = make_manager(tmp_path, env={"XG_GLM_API_KEY": "gk"})
        assert reloaded.active().model == "glm-4-plus"

    def test_set_config_value_dotted(self, tmp_path):
        manager = make_manager(tmp_path, env={})
        manager.set_config_value("providers.deepseek.default_model", "deepseek-reasoner")

        user_cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert user_cfg["providers"]["deepseek"]["default_model"] == "deepseek-reasoner"

        reloaded = make_manager(tmp_path, env={"XG_DEEPSEEK_API_KEY": "dk"})
        assert reloaded.resolve_provider("deepseek").default_model == "deepseek-reasoner"

    def test_get_config_value(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={"active_provider": "glm", "providers": {"glm": {"context_window": 64000}}},
        )
        assert manager.get_config_value("active_provider") == "glm"
        assert manager.get_config_value("providers.glm.context_window") == "64000"
        assert manager.get_config_value("nope.x") is None


class TestMaskKey:
    def test_mask(self):
        assert mask_key("sk-abcdef") == "sk-a****"
        assert mask_key("") == "(未配置)"
        assert mask_key("abc") == "****"
