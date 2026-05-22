"""共享测试夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

from xg.config.settings import Settings
from xg.tool.builtin import build_registry


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """临时项目目录，预置若干文件。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def main():\n    print('hello')\n", encoding="utf-8"
    )
    (tmp_path / "src" / "util.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(tmp_project: Path) -> object:
    return build_registry(base_dir=tmp_project)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_base="https://api.test/v1",
        api_key="sk-test",
        model="test-model",
        context_window=128_000,
        tool_steps=20,
    )
