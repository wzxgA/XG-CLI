"""CLI 斜杠命令单元测试：/model 与 /config。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from xg.agent.react import ReActAgent
from xg.cli.app import _handle_command
from xg.cli.commands import SLASH_COMMANDS, filter_slash_commands
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
    manager = ConfigManager(
        user_dir=user_dir, project_dir=project_dir, env=dict(env), load_env=False
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
        assert not (tmp_path / "user_xg" / "config.json").exists()

    def test_switch_without_any_key_rejected(self, tmp_path):
        """完全没有 Key 时拒绝切换。"""
        agent, settings, manager = make_context(tmp_path, {})
        output = run_cmd(agent, settings, manager, "/model deepseek")
        assert "缺少" in output
        assert settings.provider == "openai"
        assert isinstance(agent.llm, DummyClient)
        assert not (tmp_path / "user_xg" / "config.json").exists()

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


def test_slash_command_catalog_is_stable_and_prefix_filtered():
    assert [spec.name for spec in filter_slash_commands("/")][:4] == [
        "/plan", "/model", "/config", "/memory"
    ]
    assert [spec.name for spec in filter_slash_commands("/m")] == ["/model", "/memory"]
    assert [spec.name for spec in filter_slash_commands("/PL")] == ["/plan"]
    assert [spec.name for spec in filter_slash_commands("/c")] == ["/cancel"]
    assert filter_slash_commands("查看 /model") == ()
    assert filter_slash_commands("/memory list") == ()
    assert len({spec.name for spec in SLASH_COMMANDS}) == len(SLASH_COMMANDS)
    assert all(spec.name.startswith("/") for spec in SLASH_COMMANDS)
