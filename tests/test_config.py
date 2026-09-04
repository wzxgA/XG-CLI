"""ConfigManager 单元测试：三层合并、持久化、旧环境变量兼容。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xg.config.manager import ConfigManager, ProviderNotConfigured, mask_key
from xg.config.settings import load_settings

from tests.conftest import seed_config


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
    # 用户配置：仅在显式提供 user_cfg 或该文件尚未存在时写入（注入默认自定义 providers）。
    # 已存在时（如 set_active/set_config_value 持久化后重载）保留文件原状，避免冲掉测试改动。
    cfg_path = user_dir / "config.json"
    if user_cfg is not None or not cfg_path.exists():
        cfg_path.write_text(
            json.dumps(seed_config(user_cfg if user_cfg is not None else {})),
            encoding="utf-8",
        )
    if project_cfg is not None:
        (project_dir / "config.json").write_text(
            json.dumps(seed_config(project_cfg)), encoding="utf-8"
        )
    return ConfigManager(
        user_dir=user_dir,
        project_dir=project_dir,
        env=dict(env or {}),
        load_env=False,
    )


class TestBaseProvider:
    def test_missing_provider_fails_fast(self, tmp_path):
        """未配置 XG_PROVIDER / active_provider 时不再隐式用 openai，而是抛错提示。"""
        manager = make_manager(
            tmp_path,
            env={"XG_API_BASE": "https://legacy.test/v1", "XG_OPENAI_API_KEY": "k-123"},
        )
        with pytest.raises(ProviderNotConfigured):
            manager.active()

    def test_empty_env_requires_explicit_provider(self, tmp_path):
        manager = make_manager(tmp_path, env={})
        with pytest.raises(ProviderNotConfigured):
            manager.active()

    def test_explicit_base_provider_uses_legacy_base_for_openai(self, tmp_path):
        """显式 XG_PROVIDER=openai 时，旧 XG_API_BASE 兼容兜底仍生效。"""
        manager = make_manager(
            tmp_path,
            env={"XG_PROVIDER": "openai", "XG_API_BASE": "https://legacy.test/v1",
                 "XG_OPENAI_API_KEY": "k-123", "XG_MODEL": "old-model"},
        )
        active = manager.active()
        assert active.provider_name == "openai"
        assert active.api_base == "https://legacy.test/v1"
        assert active.api_key == "k-123"
        assert active.model == "old-model"


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
        manager = make_manager(tmp_path, env={"XG_PROVIDER": "openai", "XG_API_KEY": "legacy-key"})
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

    def test_env_provider_unknown_fails_fast(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_PROVIDER": "nope", "XG_API_KEY": "k"})
        with pytest.raises(ProviderNotConfigured):
            manager.active()


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
            env={"XG_PROVIDER": "openai", "XG_API_BASE": "https://env.test/v1", "XG_API_KEY": "k"},
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
            env={"XG_PROVIDER": "openai", "XG_API_KEY": "k", "XG_API_BASE": "https://legacy.test/v1",
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


class TestTuiSettings:
    def test_refresh_fps_defaults_to_twenty_and_reads_environment(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_TUI_REFRESH_FPS": "30"})
        assert load_settings(manager).tui_refresh_fps == 30

    @pytest.mark.parametrize("raw", ["0", "4", "61", "not-a-number"])
    def test_refresh_fps_is_safe_for_invalid_or_out_of_range_values(self, tmp_path, raw):
        manager = make_manager(tmp_path, env={"XG_TUI_REFRESH_FPS": raw})
        settings = load_settings(manager)
        assert 5 <= settings.tui_refresh_fps <= 60
        if raw == "not-a-number":
            assert settings.tui_refresh_fps == 20

    def test_skill_settings_follow_skill_config_file_and_environment(self, tmp_path):
        manager = make_manager(tmp_path, env={"XG_SKILLS_MAX_CHARS": "888"})
        (tmp_path / "user_xg" / "skills.json").write_text(
            json.dumps({"max_index_items": 7, "max_loaded_chars": 4321}), encoding="utf-8"
        )
        settings = load_settings(manager)
        assert settings.skills_max_index_items == 7
        assert settings.skills_max_loaded_chars == 4321
        assert settings.skills_max_chars == 888


class TestSmartRouterConfig:
    def test_defaults_when_missing(self, tmp_path):
        manager = make_manager(tmp_path, env={})
        cfg = manager.smart_router_config()
        assert cfg == {"enabled": False, "tiers": {}}

    def test_reads_enabled_and_tiers(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={
                "smart_router": {
                    "enabled": True,
                    "tiers": {
                        "Basic": {"provider": "deepseek", "model": "deepseek-chat"},
                        "Superior": {"provider": "glm", "model": "glm-4-plus"},
                    },
                }
            },
        )
        cfg = manager.smart_router_config()
        assert cfg["enabled"] is True
        assert cfg["tiers"]["Basic"] == {"provider": "deepseek", "model": "deepseek-chat"}
        assert cfg["tiers"]["Superior"]["provider"] == "glm"

    def test_project_level_overrides_user_level(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={"smart_router": {"enabled": True, "tiers": {"Basic": {"provider": "deepseek"}}}},
            project_cfg={"smart_router": {"tiers": {"Basic": {"provider": "glm"}}}},
        )
        cfg = manager.smart_router_config()
        assert cfg["enabled"] is True  # 用户级未被项目级覆盖的键保留
        assert cfg["tiers"]["Basic"] == {"provider": "glm"}

    @pytest.mark.parametrize("raw", ["on", "1", "true", "True"])
    def test_enabled_accepts_string_truthy(self, tmp_path, raw):
        manager = make_manager(tmp_path, env={}, user_cfg={"smart_router": {"enabled": raw}})
        assert manager.smart_router_config()["enabled"] is True

    def test_invalid_structures_are_dropped_silently(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={
                "smart_router": {
                    "enabled": "yes-unknown",
                    "tiers": {
                        "Basic": "not-a-dict",
                        "Enhanced": {"provider": 123, "model": None},
                        "Superior": {"provider": "  ", "model": "glm-4-flash"},  # 空 provider 被丢、model 保留
                        "Ultimate": {},
                    },
                }
            },
        )
        cfg = manager.smart_router_config()
        assert cfg["enabled"] is False
        assert "Basic" not in cfg["tiers"]
        assert "Enhanced" not in cfg["tiers"]
        assert cfg["tiers"]["Superior"] == {"model": "glm-4-flash"}
        assert "Ultimate" not in cfg["tiers"]  # 空条目不产生 configured 污染

    def test_non_dict_smart_router_node(self, tmp_path):
        manager = make_manager(tmp_path, env={}, user_cfg={"smart_router": ["broken"]})
        assert manager.smart_router_config() == {"enabled": False, "tiers": {}}

    def test_set_smart_router_enabled_persists(self, tmp_path):
        manager = make_manager(tmp_path, env={})
        manager.set_smart_router_enabled(True)
        user_cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert user_cfg["smart_router"]["enabled"] is True

        # 关闭时保留 tiers 节，只翻转 enabled
        manager.set_smart_router_enabled(False)
        user_cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert user_cfg["smart_router"]["enabled"] is False

    def test_set_smart_router_enabled_keeps_existing_tiers(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={},
            user_cfg={"smart_router": {"tiers": {"Basic": {"provider": "deepseek", "model": "deepseek-chat"}}}},
        )
        manager.set_smart_router_enabled(True)
        cfg = manager.smart_router_config()
        assert cfg["enabled"] is True
        assert cfg["tiers"]["Basic"]["model"] == "deepseek-chat"


class TestSmartRouterEnvTiers:
    """SmartRouter 档位可通过 .env 环境变量配置（覆盖 config.json 同档逐键）。"""

    def test_env_only_provider_and_model(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={
                "XG_SMART_ROUTER_BASIC_PROVIDER": "deepseek",
                "XG_SMART_ROUTER_BASIC_MODEL": "deepseek-chat",
                "XG_SMART_ROUTER_ULTIMATE_PROVIDER": "glm",
                "XG_SMART_ROUTER_ULTIMATE_MODEL": "glm-4-plus",
            },
        )
        cfg = manager.smart_router_config()
        assert cfg["tiers"]["Basic"] == {"provider": "deepseek", "model": "deepseek-chat"}
        assert cfg["tiers"]["Ultimate"] == {"provider": "glm", "model": "glm-4-plus"}
        assert "Enhanced" not in cfg["tiers"]  # 未配置不产生条目
        assert "Superior" not in cfg["tiers"]

    def test_env_overrides_config_same_key(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_SMART_ROUTER_BASIC_PROVIDER": "deepseek"},
            user_cfg={"smart_router": {"tiers": {"Basic": {"provider": "openai", "model": "gpt-4o-mini"}}}},
        )
        cfg = manager.smart_router_config()
        # env 覆盖 provider，config 的 model 保留（逐键合并）
        assert cfg["tiers"]["Basic"] == {"provider": "deepseek", "model": "gpt-4o-mini"}

    def test_env_blank_value_ignored(self, tmp_path):
        manager = make_manager(
            tmp_path,
            env={"XG_SMART_ROUTER_BASIC_PROVIDER": "  ", "XG_SMART_ROUTER_BASIC_MODEL": ""},
            user_cfg={"smart_router": {"tiers": {"Basic": {"provider": "openai", "model": "gpt-4o-mini"}}}},
        )
        cfg = manager.smart_router_config()
        # 空白 env 视作未设置，保留 config 值
        assert cfg["tiers"]["Basic"] == {"provider": "openai", "model": "gpt-4o-mini"}


class TestSmartRouterSettings:
    def test_smart_router_disabled_by_default(self, tmp_path):
        settings = load_settings(make_manager(tmp_path, env={}))
        assert settings.smart_router_enabled is False
        assert settings.smart_router_saved is None

    @pytest.mark.parametrize("raw", ["on", "1", "true"])
    def test_smart_router_env_enables(self, tmp_path, raw):
        settings = load_settings(make_manager(tmp_path, env={"XG_SMART_ROUTER": raw}))
        assert settings.smart_router_enabled is True

    def test_smart_router_env_off(self, tmp_path):
        settings = load_settings(make_manager(tmp_path, env={"XG_SMART_ROUTER": "off"}))
        assert settings.smart_router_enabled is False
