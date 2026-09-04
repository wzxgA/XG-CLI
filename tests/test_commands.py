"""CLI 斜杠命令单元测试：/model 与 /config。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from xg.agent.react import ReActAgent
from xg.cli.app import _handle_command
from xg.cli.commands import CommandContext, CommandService, SLASH_COMMANDS, filter_slash_commands
from xg.cli.help import format_command_help, format_help

from tests.conftest import seed_config
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.llm.client import LlmClient
from xg.llm.types import StreamEvent
from xg.tool.builtin import build_registry


class DummyClient(LlmClient):
    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="done")
        return


def make_context(tmp_path: Path, env: dict) -> tuple[ReActAgent, Settings, ConfigManager]:
    user_dir = tmp_path / "user_xg"
    project_dir = tmp_path / "proj_xg"
    user_dir.mkdir(exist_ok=True)
    project_dir.mkdir(exist_ok=True)
    # 命令测试以 openai 作为显式 base provider；未提供时默认 openai（不再隐式兜底）。
    # 注入默认自定义 providers 保证 manager 可解析；已有配置（如 smart_router）保留。
    cfg_path = user_dir / "config.json"
    existing = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg_path.write_text(json.dumps(seed_config(existing)), encoding="utf-8")
    full_env = {"XG_PROVIDER": "openai", **dict(env)}
    manager = ConfigManager(
        user_dir=user_dir, project_dir=project_dir, env=dict(full_env), load_env=False
    )
    settings = Settings(
        provider="openai",
        api_base=env.get("XG_API_BASE", "https://api.openai.com/v1"),
        api_key=env.get("XG_OPENAI_API_KEY", ""),
        model=env.get("XG_MODEL", "gpt-4o-mini"),
        context_window=128_000,
    )
    agent = ReActAgent(llm=DummyClient(), tools=build_registry(base_dir=tmp_path), settings=settings)
    return agent, settings, manager


def run_cmd(agent, settings, manager, raw: str) -> str:
    message, should_exit = _handle_command(agent, settings, manager, raw)
    assert not should_exit
    return message


class TestModelCommand:
    def test_list(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/model")
        assert "当前: openai / gpt-4o-mini" in output
        assert "deepseek" in output
        assert "glm" in output

    def test_list_keyword_does_not_switch(self, tmp_path):
        # `list` and `provider` are declared subcommands, not model names.
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        for sub in ("list", "provider"):
            output = run_cmd(agent, settings, manager, f"/model {sub}")
            assert "当前:" in output and "可用 providers:" in output
            assert output.strip().startswith("当前:")
            # 不应被当作模型切换
            assert "已切换" not in output
        assert settings.provider != "deepseek"

    def test_switch_provider(self, tmp_path):
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "已切换: DeepSeek / deepseek-chat" in output
        assert settings.provider == "deepseek"
        assert settings.api_base == "https://api.deepseek.com/v1"
        assert settings.api_key == "dk"
        assert settings.context_window == 128_000
        assert agent.llm.model == "deepseek-chat"  # type: ignore[attr-defined]
        # 持久化
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert cfg["active_provider"] == "deepseek"

    def test_switch_provider_with_model(self, tmp_path):
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_GLM_API_KEY": "gk"}
        )
        output = run_cmd(agent, settings, manager, "/model glm/glm-4-plus")
        assert "已切换: GLM / glm-4-plus" in output
        assert agent.llm.model == "glm-4-plus"  # type: ignore[attr-defined]

    def test_switch_respects_per_provider_url(self, tmp_path):
        agent, settings, manager = make_context(
            tmp_path,
            {
                "XG_OPENAI_API_KEY": "k",
                "XG_DEEPSEEK_API_KEY": "dk",
                "XG_DEEPSEEK_API_BASE": "https://my-proxy.test/v1",
            },
        )
        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "已切换: DeepSeek / deepseek-chat" in output
        assert settings.api_base == "https://my-proxy.test/v1"
        assert agent.llm.api_base == "https://my-proxy.test/v1"  # type: ignore[attr-defined]

    def test_switch_model_within_provider(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/model gpt-4o")
        assert "已切换: OpenAI / gpt-4o" in output
        assert settings.model == "gpt-4o"

    def test_switch_requires_specific_key(self, tmp_path):
        """无通用兜底：目标 provider 未配专属 Key 时拒绝切换。"""
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "缺少 XG_DEEPSEEK_API_KEY" in output
        assert settings.provider == "openai"
        assert isinstance(agent.llm, DummyClient)
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert "active_provider" not in cfg  # 拒绝的切换不应持久化

    def test_switch_without_any_key_rejected(self, tmp_path):
        """完全没有 Key 时拒绝切换。"""
        agent, settings, manager = make_context(tmp_path, {})
        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "缺少" in output
        assert settings.provider == "openai"
        assert isinstance(agent.llm, DummyClient)
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert "active_provider" not in cfg  # 拒绝的切换不应持久化

    def test_unknown_token_treated_as_model_name(self, tmp_path):
        """不在 provider 列表中的参数，按当前 provider 内切换模型处理。"""
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/model my-fancy-model")
        assert "已切换: OpenAI / my-fancy-model" in output
        assert settings.model == "my-fancy-model"

    def test_custom_provider_switchable(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_CUSTOM_API_KEY": "ck"})
        manager.set_config_value(
            "providers.myproxy.api_base", "https://my-proxy.test/v1"
        )
        manager.set_config_value("providers.myproxy.api_key_env", "XG_CUSTOM_API_KEY")
        manager.set_config_value("providers.myproxy.default_model", "my-model")
        output = run_cmd(agent, settings, manager, "/model myproxy")
        assert "已切换: myproxy / my-model" in output
        assert settings.api_base == "https://my-proxy.test/v1"


class TestConfigCommand:
    def test_overview_masks_key(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "sk-abcdef"})
        output = run_cmd(agent, settings, manager, "/config")
        assert "api_key:  sk-a****" in output
        assert "sk-abcdef" not in output

    def test_list(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/config list")
        assert "openai" in output
        assert "deepseek" in output
        assert "glm" in output
        assert "kimi" in output

    def test_get(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        manager.set_config_value("providers.deepseek.default_model", "deepseek-reasoner")
        output = run_cmd(agent, settings, manager, "/config get providers.deepseek.default_model")
        assert "deepseek-reasoner" in output

    def test_set_active_provider_switches(self, tmp_path):
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        output = run_cmd(agent, settings, manager, "/config set active_provider deepseek")
        assert "已切换: DeepSeek" in output
        assert settings.provider == "deepseek"

    def test_set_active_model(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/config set active_model gpt-4o")
        assert "已切换: OpenAI / gpt-4o" in output
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert cfg["active_model"] == "gpt-4o"

    def test_set_other_key_persists(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/config set providers.openai.default_model gpt-4o")
        assert "已设置 providers.openai.default_model = gpt-4o" in output


class TestOtherCommands:
    def test_help_lists_command_metadata_and_shortcuts(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/help")
        assert output.startswith("XG 命令帮助\n")
        assert "配置与能力" in output
        assert "/mcp status|restart|logs|enable|disable|resources" in output
        assert "管理 MCP Server" in output
        assert "/? = /help" in output
        assert "Ctrl+C" in output
        assert "f1" not in output.lower()
        assert len(agent.messages) == 1

    def test_help_alias_and_single_command_details(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/? mcp")
        assert output.startswith("/mcp — 管理 MCP Server\n")
        assert "子命令" in output
        assert "/mcp restart <server>" in output
        assert "示例" in output
        assert "/mcp resources" in output

    def test_help_details_cover_command_modes_and_examples(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/help memory")
        assert "/memory — 管理长期记忆" in output
        assert "/memory list [limit]" in output
        assert "/memory search <关键词>" in output
        assert "/memory delete <id>" in output
        assert "/memory clear" in output
        assert "/memory list 10" in output

        output = run_cmd(agent, settings, manager, "/help model")
        assert "/model <provider>/<model>" in output
        assert "/model deepseek/deepseek-chat" in output

    def test_help_unknown_command_is_actionable(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/help missing")
        assert "未找到命令帮助" in output
        assert "/help 查看全部命令" in output

    def test_unknown_command(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        message, should_exit = _handle_command(agent, settings, manager, "/foo")
        assert "未知命令" in message
        assert not should_exit

    def test_exit(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        _, should_exit = _handle_command(agent, settings, manager, "/exit")
        assert should_exit

    def test_clear(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        message, should_exit = _handle_command(agent, settings, manager, "/clear")
        assert "已清空" in message
        assert len(agent.messages) == 1


class TestSmartRouterCommand:
    """phase-01 子步骤 C：/smartRouter 命令 + 手动 /model 优先接管。"""

    def test_status_when_off_by_default(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/smartRouter")
        assert "SmartRouter: 关闭" in output
        assert "Basic" in output and "Ultimate" in output

    def test_on_saves_snapshot_and_persists(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/smartRouter on")
        assert "已开启" in output
        assert settings.smart_router_enabled is True
        assert settings.smart_router_saved == ("openai", "gpt-4o-mini")
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert cfg["smart_router"]["enabled"] is True

    def test_on_again_is_idempotent(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        run_cmd(agent, settings, manager, "/smartRouter on")
        output = run_cmd(agent, settings, manager, "/smartRouter on")
        assert "已开启" in output  # 幂等提示
        assert settings.smart_router_saved == ("openai", "gpt-4o-mini")  # 快照未被覆盖

    def test_off_restores_saved_model(self, tmp_path):
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        # 先切到 deepseek，再开启 smartRouter（快照为 deepseek/deepseek-chat）
        run_cmd(agent, settings, manager, "/model deepseek")
        run_cmd(agent, settings, manager, "/smartRouter on")
        assert settings.provider == "deepseek"
        # 手动用 /model 再切到 glm（不应走 smartRouter 关闭还原逻辑，这里直接用 off 验证恢复快照）
        run_cmd(agent, settings, manager, "/smartRouter off")
        assert settings.smart_router_enabled is False
        # 快照恢复：回到开启前的 deepseek
        assert settings.provider == "deepseek"
        assert settings.model == "deepseek-chat"
        assert settings.smart_router_saved is None

    def test_off_when_already_off(self, tmp_path):
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        output = run_cmd(agent, settings, manager, "/smartRouter off")
        assert "已关闭" in output
        assert settings.smart_router_enabled is False

    def test_status_shows_configured_and_fallback(self, tmp_path):
        # 配置 Basic 用 deepseek（配了 key），Ultimate 用 glm（无 key → 校验失败回落 openai）
        user_cfg = tmp_path / "user_xg" / "config.json"
        user_cfg.parent.mkdir(exist_ok=True)
        user_cfg.write_text(
            json.dumps(
                {
                    "smart_router": {
                        "tiers": {
                            "Basic": {"provider": "deepseek", "model": "deepseek-chat"},
                            "Ultimate": {"provider": "glm", "model": "glm-4-plus"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        output = run_cmd(agent, settings, manager, "/smartRouter status")
        assert "Basic    → deepseek/deepseek-chat  OK" in output
        # Ultimate 配置了 glm 但无 key → 回落 openai/gpt-4o-mini 并标 (x)
        assert "Ultimate → openai/gpt-4o-mini  (x)" in output

    def test_manual_model_switch_disables_smart_router(self, tmp_path):
        """手动 /model 优先：切换成功自动关闭 SmartRouter 并清除快照。"""
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_DEEPSEEK_API_KEY": "dk"}
        )
        run_cmd(agent, settings, manager, "/smartRouter on")
        assert settings.smart_router_enabled is True

        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "已切换: DeepSeek" in output
        assert "SmartRouter 已自动关闭" in output
        assert settings.smart_router_enabled is False
        assert settings.smart_router_saved is None
        # 持久化同步关闭
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert cfg["smart_router"]["enabled"] is False

    def test_failed_switch_keeps_smart_router(self, tmp_path):
        """/model 切换失败（缺 key）不应关闭 SmartRouter。"""
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        run_cmd(agent, settings, manager, "/smartRouter on")
        output = run_cmd(agent, settings, manager, "/model deepseek")  # 无 deepseek key
        assert "缺少 XG_DEEPSEEK_API_KEY" in output
        assert settings.smart_router_enabled is True  # 保持开启


class TestSmartRouterRouting:
    """phase-01 子步骤 D：主循环路由挂点 + 不持久化换模型。"""

    def test_route_switches_model_and_does_not_persist(self, tmp_path):
        from xg.cli.app import _route_user_turn

        user_cfg = tmp_path / "user_xg" / "config.json"
        user_cfg.parent.mkdir(exist_ok=True)
        user_cfg.write_text(
            json.dumps(
                {
                    "smart_router": {
                        "enabled": True,
                        "tiers": {
                            "Superior": {"provider": "deepseek", "model": "deepseek-chat"},
                            "Ultimate": {"provider": "glm", "model": "glm-4-plus"},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        agent, settings, manager = make_context(
            tmp_path, {"XG_OPENAI_API_KEY": "k", "XG_GLM_API_KEY": "gk"}
        )
        # 输入命中架构+风险 → Ultimate，配置用 glm → 应切到 glm/glm-4-plus
        tier, ts = _route_user_turn(
            agent, settings, manager, "设计日活千万的推荐系统架构并给出部署回滚方案",
            prev_tier=None, prev_ts=None,
        )
        assert tier == "Ultimate"
        assert settings.provider == "glm"
        assert settings.model == "glm-4-plus"
        assert agent.llm.model == "glm-4-plus"  # type: ignore[attr-defined]
        # 关键：不写回持久化 active_provider/active_model
        cfg = json.loads((tmp_path / "user_xg" / "config.json").read_text(encoding="utf-8"))
        assert "active_provider" not in cfg

    def test_route_same_model_keeps_client(self, tmp_path):
        from xg.cli.app import _route_user_turn

        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        orig_llm = agent.llm
        tier, _ = _route_user_turn(agent, settings, manager, "你好", None, None)
        assert tier == "Basic"
        # fallback 与当前相同 → 不重建客户端
        assert agent.llm is orig_llm
        assert settings.provider == "openai"

    def test_route_unknown_provider_falls_back_on_error(self, tmp_path):
        from xg.cli.app import _route_user_turn

        user_cfg = tmp_path / "user_xg" / "config.json"
        user_cfg.parent.mkdir(exist_ok=True)
        user_cfg.write_text(
            json.dumps(
                {
                    "smart_router": {"tiers": {"Ultimate": {"provider": "nope", "model": "x"}}}
                }
            ),
            encoding="utf-8",
        )
        agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
        tier, _ = _route_user_turn(
            agent, settings, manager, "设计日活千万的推荐系统架构并给出部署回滚方案", None, None
        )
        # Ultimate 配置了 nope（无 key）→ 回落 active openai，路由仍给出 Ultiimate，客户端不换
        assert settings.provider == "openai"
        assert isinstance(agent.llm, DummyClient)


def test_slash_command_catalog_is_stable_and_prefix_filtered():
    assert [spec.name for spec in filter_slash_commands("/")][:5] == [
        "/plan", "/team", "/model", "/config", "/mcp"
    ]
    assert [spec.name for spec in filter_slash_commands("/m")] == ["/model", "/mcp", "/memory"]
    assert [spec.name for spec in filter_slash_commands("/PL")] == ["/plan"]
    assert [spec.name for spec in filter_slash_commands("/c")] == ["/cancel"]
    assert filter_slash_commands("查看 /model") == ()
    assert filter_slash_commands("/memory list") == ()
    assert len({spec.name for spec in SLASH_COMMANDS}) == len(SLASH_COMMANDS)
    assert all(spec.name.startswith("/") for spec in SLASH_COMMANDS)


def test_help_formatting_is_catalog_driven_and_can_omit_shortcuts():
    output = format_help(include_shortcuts=False)
    for spec in SLASH_COMMANDS:
        assert spec.usage in output
        assert spec.description in output
    assert "TUI 快捷键" not in output
    assert "f1" not in output.lower()


def test_team_help_describes_resume_scope():
    output = format_command_help("team")

    assert "/team <任务>" in output
    assert "/team resume <任务ID> --write-scope <范围>" in output
    assert "needs_input" in output
    assert "Repairer" in output
    assert "/team resume t4 --write-scope xg/auth/*.py" in output


def test_full_help_keeps_team_top_level_entry():
    output = format_help(include_shortcuts=False)

    assert "/team <任务>" in output


def test_command_help_accepts_name_or_alias():
    assert format_command_help("cancel").startswith("/cancel —")
    assert format_command_help("/c").startswith("/cancel —")


@pytest.mark.asyncio
async def test_command_service_uses_shared_help_formatter(tmp_path):
    agent, settings, manager = make_context(tmp_path, {"XG_OPENAI_API_KEY": "k"})
    result = await CommandService(CommandContext(agent, settings, manager)).execute("/help model")
    assert result.ok is True
    assert result.message.startswith("/model —")
    assert "用法：/model [provider] [model]" in result.message
