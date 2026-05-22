"""配置读取：.env 文件 + 环境变量，环境变量优先。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    """运行时配置。model 可被 /model 命令运行时修改。"""

    api_base: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 128_000
    tool_steps: int = 20
    # token 预算阈值：messages 估算 token 超过 window * budget_ratio 时终止循环
    budget_ratio: float = 0.8
    # 工具输出超出该字符数则截断，防止撑爆上下文
    max_tool_output_chars: int = 20_000
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def token_budget(self) -> int:
        return int(self.context_window * self.budget_ratio)

    def estimate_tokens(self, text: str) -> int:
        """字符近似估算：ASCII 约 4 字符/token，非 ASCII（如中文）约 1 字符/token。"""
        if not text:
            return 0
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        non_ascii = len(text) - ascii_chars
        return ascii_chars // 4 + non_ascii


def load_settings(env_file: str | Path | None = None) -> Settings:
    """加载配置。优先级：环境变量 > .env 文件 > 默认值。"""
    if env_file is None:
        # 从当前工作目录向上查找 .env（最多 3 层）
        env_file = _find_env_file()
    if env_file and Path(env_file).is_file():
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)  # 仅读进程已有环境变量

    return Settings(
        api_base=os.environ.get("XG_API_BASE", "").rstrip("/"),
        api_key=os.environ.get("XG_API_KEY", ""),
        model=os.environ.get("XG_MODEL", ""),
        context_window=_get_int("XG_CONTEXT_WINDOW", 128_000),
        tool_steps=_get_int("XG_TOOL_STEPS", 20),
    )


def _find_env_file() -> Path | None:
    cur = Path.cwd()
    for _ in range(3):
        candidate = cur / ".env"
        if candidate.is_file():
            return candidate
        if cur == cur.parent:
            break
        cur = cur.parent
    return None


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default
