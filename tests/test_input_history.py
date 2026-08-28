from __future__ import annotations

import json

from xg.input_history import HistoryConfig, InputHistory, PromptToolkitHistory
from xg.input_history.persistence import history_path, project_scope


def test_history_navigation_restores_draft_and_has_stable_boundaries(tmp_path):
    history = InputHistory(project_root=tmp_path, config=HistoryConfig(persist=False))
    assert history.add("第一条") is True
    assert history.add("第二条") is True
    assert history.add("第二条") is False

    assert history.previous("新草稿") == "第二条"
    assert history.previous("ignored") == "第一条"
    assert history.previous("ignored") == "第一条"
    assert history.next() == "第二条"
    assert history.next() == "新草稿"
    assert history.next() == "新草稿"


def test_history_is_bounded_and_disabled_history_does_not_load_or_write(tmp_path):
    config = HistoryConfig(max_entries=2, max_entry_chars=4, persist=True)
    history = InputHistory(project_root=tmp_path, user_dir=tmp_path / "user", config=config)
    assert history.add("12345") is False
    assert history.add("一") is True
    assert history.add("二") is True
    assert history.add("三") is True
    assert [entry.text for entry in history.entries()] == ["二", "三"]
    assert history_path(tmp_path / "user", project_scope(tmp_path)).is_file()

    disabled = InputHistory(
        project_root=tmp_path / "disabled", user_dir=tmp_path / "disabled-user",
        config=HistoryConfig(enabled=False),
    )
    assert disabled.entries() == ()
    assert disabled.add("不会记录") is False
    assert disabled.previous("草稿") == "草稿"


def test_sensitive_input_stays_in_memory_but_is_not_persisted(tmp_path):
    history = InputHistory(project_root=tmp_path, user_dir=tmp_path / "user")
    assert history.add("api_key=secret-value") is True
    assert history.entries()[0].persisted is False
    path = history_path(tmp_path / "user", project_scope(tmp_path))
    assert not path.exists()
    assert history.status().endswith("持久化：开启")


def test_clear_removes_memory_and_persistent_file(tmp_path):
    history = InputHistory(project_root=tmp_path, user_dir=tmp_path / "user")
    history.add("普通任务")
    path = history_path(tmp_path / "user", project_scope(tmp_path))
    assert path.exists()
    assert history.clear() == 1
    assert history.entries() == ()
    assert not path.exists()


def test_persistence_skips_corrupt_and_wrong_scope_records(tmp_path):
    user = tmp_path / "user"
    scope = project_scope(tmp_path)
    path = history_path(user, scope)
    path.parent.mkdir(parents=True)
    lines = [
        '{"text":"有效记录","created_at":"now","scope":"' + scope + '"}',
        "not-json",
        json.dumps({"text": "其他项目", "created_at": "now", "scope": "other"}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    history = InputHistory(project_root=tmp_path, user_dir=user)
    assert [entry.text for entry in history.entries()] == ["有效记录"]


def test_prompt_toolkit_adapter_uses_shared_store_and_can_pause_recording(tmp_path):
    history = InputHistory(project_root=tmp_path, config=HistoryConfig(persist=False))
    adapter = PromptToolkitHistory(history)
    adapter.recording_enabled = False
    adapter.append_string("审批答案")
    assert history.entries() == ()
    adapter.recording_enabled = True
    adapter.append_string("普通任务")
    assert [entry.text for entry in history.entries()] == ["普通任务"]
    assert list(adapter.load_history_strings()) == ["普通任务"]
