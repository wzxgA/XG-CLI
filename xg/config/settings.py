"""运行时配置快照与加载。

Settings 是解析后的运行时快照（provider / model / key / 窗口等），
由 ConfigManager 按「默认 < 用户级 < 项目级 < 环境变量」合并产出。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xg.config.manager import ConfigManager


@dataclass
class Settings:
    """运行时配置快照。provider / model 可被 /model 命令运行时修改。"""

    provider: str = ""
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 128_000
    tool_steps: int = 20
    # token 预算阈值：messages 估算 token 超过 window * budget_ratio 时终止循环
    budget_ratio: float = 0.8
    # 第 5 期：自动压缩时保留的最近完整对话轮次
    context_keep_recent_turns: int = 4
    # 第 5 期：摘要输出动态预留上限
    context_summary_max_tokens: int = 4096
    # 第 5 期：自动注入长期记忆的字符上限
    memory_prompt_max_chars: int = 8000
    # 第 5 期：单个 XG.md/XG.local.md 的读取字符上限
    project_memory_max_chars: int = 32000
    # 工具输出超出该字符数则截断，防止撑爆上下文
    max_tool_output_chars: int = 20_000
    # 并行工具执行并发数（第 3 期）
    max_parallel: int = 4
    # 单工具执行超时（秒，第 3 期）
    tool_timeout: float = 120.0
    # HITL 审批默认开启（第 3 期）
    hitl: bool = True
    # 计划模式（第 4 期）：子任务数上限
    plan_max_subtasks: int = 12
    # 计划模式：每个子任务的最大工具步数
    plan_subtask_steps: int = 10
    # 计划模式：计划级允许的子任务失败数（超出终止剩余批次）
    plan_max_failures: int = 3
    # Textual TUI 最大刷新频率（第 6 期）
    tui_refresh_fps: int = 20
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


def load_settings(manager: ConfigManager | None = None) -> Settings:
    """加载配置并产出运行时快照。"""
    manager = manager or ConfigManager()
    active = manager.active()
    return Settings(
        provider=active.provider_name,
        api_base=active.api_base,
        api_key=active.api_key,
        model=active.model,
        context_window=active.context_window,
        tool_steps=_get_int(manager.env, "XG_TOOL_STEPS", 20),
        budget_ratio=_clamp_float(_get_float(manager.env, "XG_CONTEXT_BUDGET_RATIO", 0.8), 0.5, 0.9),
        context_keep_recent_turns=max(0, _get_int(manager.env, "XG_CONTEXT_KEEP_RECENT_TURNS", 4)),
        context_summary_max_tokens=max(512, _get_int(manager.env, "XG_CONTEXT_SUMMARY_MAX_TOKENS", 4096)),
        memory_prompt_max_chars=max(256, _get_int(manager.env, "XG_MEMORY_PROMPT_MAX_CHARS", 8000)),
        project_memory_max_chars=max(256, _get_int(manager.env, "XG_PROJECT_MEMORY_MAX_CHARS", 32000)),
        max_parallel=_get_int(manager.env, "XG_MAX_PARALLEL", 4),
        tool_timeout=_get_float(manager.env, "XG_TOOL_TIMEOUT", 120.0),
        hitl=manager.env.get("XG_HITL", "on").lower() not in ("off", "0", "false"),
        plan_max_subtasks=_get_int(manager.env, "XG_PLAN_MAX_SUBTASKS", 12),
        plan_subtask_steps=_get_int(manager.env, "XG_PLAN_SUBTASK_STEPS", 10),
        plan_max_failures=_get_int(manager.env, "XG_PLAN_MAX_FAILURES", 3),
        tui_refresh_fps=max(5, min(60, _get_int(manager.env, "XG_TUI_REFRESH_FPS", 20))),
    )


def _get_float(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name, "")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _get_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
