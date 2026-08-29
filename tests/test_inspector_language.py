"""Inspector language preference, command, and rendering tests."""

from __future__ import annotations

import json

import pytest

from xg.cli.commands import CommandContext, CommandService
from xg.config.manager import ConfigManager
from xg.config.settings import Settings, load_settings
from xg.tui.i18n import INSPECTOR_TEXT, normalize_language, translate_status
from xg.tui.state import (
    InspectorState,
    MemoryInspectorSnapshot,
    PlanInspectorSnapshot,
    PlanTaskSnapshot,
    SafetyInspectorSnapshot,
    TuiState,
)
from xg.tui.widgets.inspector import InspectorPanel


def make_manager(tmp_path, config: dict | None = None) -> ConfigManager:
    user_dir = tmp_path / "user_xg"
    project_dir = tmp_path / "project_xg"
    user_dir.mkdir()
    project_dir.mkdir()
    if config is not None:
        (user_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return ConfigManager(
        user_dir=user_dir,
        project_dir=project_dir,
        env={},
        load_env=False,
    )


def test_language_normalization_and_translation_fallback() -> None:
    assert normalize_language(None) == "en"
    assert normalize_language("ZH") == "zh"
    assert normalize_language("fr") == "en"
    assert set(INSPECTOR_TEXT["en"]) == set(INSPECTOR_TEXT["zh"])
    assert translate_status("zh", "running") == "执行中 (Running)"
    assert translate_status("en", "failed") == "failed"


def test_language_preference_loads_and_resets(tmp_path) -> None:
    manager = make_manager(tmp_path, {"ui_language": "zh", "active_model": "kept"})
    assert load_settings(manager).ui_language == "zh"
    assert manager.ui_language_source() == "user config"

    manager.reset_ui_language()
    config = json.loads(manager.user_config_path.read_text(encoding="utf-8"))
    assert "ui_language" not in config
    assert config["active_model"] == "kept"
    assert manager.get_ui_language() == "en"


@pytest.mark.asyncio
async def test_lang_command_changes_only_ui_preference_and_persists(tmp_path) -> None:
    manager = make_manager(tmp_path)
    settings = Settings()
    service = CommandService(CommandContext(object(), settings, manager))

    result = await service.execute("/lang zh")
    assert result.ok is True
    assert settings.ui_language == "zh"
    assert result.data == {"ui_language": "zh", "persisted": True}
    assert json.loads(manager.user_config_path.read_text(encoding="utf-8"))["ui_language"] == "zh"

    result = await service.execute("/language en")
    assert result.ok is True
    assert settings.ui_language == "en"

    result = await service.execute("/lang invalid")
    assert result.ok is False
    assert settings.ui_language == "en"

    result = await service.execute("/lang reset")
    assert result.ok is True
    assert settings.ui_language == "en"
    assert "ui_language" not in json.loads(manager.user_config_path.read_text(encoding="utf-8"))


def test_inspector_renders_all_four_views_in_chinese(tmp_path) -> None:
    state = TuiState(
        ui_language="zh",
        phase="running",
        inspector=InspectorState(
            provider="deepseek",
            model="deepseek-chat",
            active_view="safety",
            plan=PlanInspectorSnapshot(
                status="failed",
                completed_tasks=1,
                total_tasks=2,
                tasks=(PlanTaskSnapshot("t1", "读取配置", "done"),),
            ),
            memory=MemoryInspectorSnapshot(
                project_root=str(tmp_path),
                xg_loaded=True,
                memory_count=2,
            ),
            safety=SafetyInspectorSnapshot(
                hitl_enabled=False,
                approval_status="rejected",
                last_rejection="blocked command",
            ),
        ),
    )
    inspector = InspectorPanel()
    inspector.update_state(state)

    assert "Session" in inspector._view_text("session", state).plain
    assert "预估输入" in inspector._view_text("session", state).plain
    assert "失败 (failed)" in inspector._view_text("plan", state).plain
    assert "项目 Memory" in inspector._view_text("memory", state).plain
    assert "当前 approval" in inspector._view_text("safety", state).plain
    assert "HITL" in inspector._view_text("safety", state).plain
    assert "不可用" not in inspector._view_text("safety", state).plain


def test_inspector_language_switch_keeps_status_style_and_active_view() -> None:
    state = TuiState(
        inspector=InspectorState(
            active_view="safety",
            safety=SafetyInspectorSnapshot(approval_status="rejected"),
        )
    )
    inspector = InspectorPanel()
    inspector.update_state(state)
    english = inspector.render()
    inspector.set_language("zh")
    chinese = inspector.render()

    assert inspector.language == "zh"
    assert "Safety" in chinese.plain
    assert "已拒绝 (rejected)" in chinese.plain
    assert "! rejected" in english.plain
    assert any(span.style == "red" for span in chinese.spans)
