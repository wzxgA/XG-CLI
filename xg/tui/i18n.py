"""Small, dependency-free UI translations used by the Inspector."""

from __future__ import annotations

from typing import Literal


UiLanguage = Literal["en", "zh"]
SUPPORTED_UI_LANGUAGES: tuple[UiLanguage, ...] = ("en", "zh")


def normalize_language(value: object) -> UiLanguage:
    """Return a supported UI language, falling back to the default English."""
    return "zh" if str(value).strip().lower() == "zh" else "en"


INSPECTOR_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "view.session": "Session",
        "view.plan": "Plan",
        "view.memory": "Memory",
        "view.safety": "Safety",
        "label.provider": "provider",
        "label.model": "model",
        "label.status": "status",
        "label.estimated_input": "estimated input",
        "label.model_window": "model window",
        "label.window_usage": "window usage",
        "label.input_budget": "input budget",
        "label.budget_usage": "budget usage",
        "label.source": "source",
        "label.last_request": "last request",
        "label.prompt": "prompt",
        "label.completion": "completion",
        "label.total": "total",
        "label.session_usage": "session usage",
        "label.compaction": "compaction",
        "label.count": "count",
        "label.last": "last",
        "label.goal": "goal",
        "label.current_round": "current round",
        "label.progress": "progress",
        "label.failures": "failures",
        "label.project_root": "project root",
        "label.warnings": "warnings",
        "label.entries": "entries",
        "label.last_operation": "last operation",
        "label.store": "store",
        "label.session_allow_all": "session allow all",
        "label.tool": "tool",
        "label.level": "level",
        "label.decision": "decision",
        "label.reason": "reason",
        "label.last_rejection": "last rejection",
        "section.context": "Context",
        "section.last_request": "Last request",
        "section.session_usage": "Session usage",
        "section.compaction": "Compaction",
        "section.tasks": "Tasks",
        "section.project_memory": "Project memory",
        "section.long_term_memory": "Long-term memory",
        "section.current_approval": "Current approval",
        "section.last_decision": "Last decision",
        "section.policy": "Policy",
        "empty.no_plan": "No plan",
        "unit.token": "token",
        "unit.tasks": "tasks",
        "unit.round": "Round {current} / {total}",
    },
    "zh": {
        "view.session": "Session",
        "view.plan": "Plan",
        "view.memory": "Memory",
        "view.safety": "Safety",
        "label.provider": "Provider",
        "label.model": "Model",
        "label.status": "Status",
        "label.estimated_input": "预估输入",
        "label.model_window": "上下文窗口",
        "label.window_usage": "窗口使用率",
        "label.input_budget": "输入预算",
        "label.budget_usage": "预算使用率",
        "label.source": "来源",
        "label.last_request": "最近一次请求",
        "label.prompt": "Prompt",
        "label.completion": "Completion",
        "label.total": "Total",
        "label.session_usage": "Session 使用量",
        "label.compaction": "Compaction",
        "label.count": "次数",
        "label.last": "最近一次",
        "label.goal": "目标",
        "label.current_round": "当前轮次",
        "label.progress": "进度",
        "label.failures": "失败次数",
        "label.project_root": "项目根目录",
        "label.warnings": "警告数",
        "label.entries": "条目数",
        "label.last_operation": "最近一次操作",
        "label.store": "Store",
        "label.session_allow_all": "Session 全部允许",
        "label.tool": "Tool",
        "label.level": "级别",
        "label.decision": "Decision",
        "label.reason": "原因",
        "label.last_rejection": "最近一次拒绝",
        "section.context": "Context",
        "section.last_request": "最近一次请求",
        "section.session_usage": "Session 使用量",
        "section.compaction": "Compaction",
        "section.tasks": "Tasks",
        "section.project_memory": "项目 Memory",
        "section.long_term_memory": "长期 Memory",
        "section.current_approval": "当前 approval",
        "section.last_decision": "最近一次决策",
        "section.policy": "Policy",
        "empty.no_plan": "暂无 Plan",
        "unit.token": "token",
        "unit.tasks": "个 Task",
        "unit.round": "第 {current} / {total} 轮",
    },
}


STATUS_ZH: dict[str, str] = {
    "idle": "空闲 (Idle)",
    "running": "执行中 (Running)",
    "working": "工作中 (Working)",
    "waiting": "等待中 (waiting)",
    "waiting approval": "等待审批 (Waiting approval)",
    "review": "审阅中 (review)",
    "plan review": "等待 Plan 审阅 (Plan review)",
    "done": "已完成 (Done)",
    "success": "成功 (success)",
    "approved": "已批准 (approved)",
    "active": "生效中 (active)",
    "on": "开启 (on)",
    "off": "关闭 (off)",
    "yes": "是 (yes)",
    "no": "否 (no)",
    "loaded": "已加载 (loaded)",
    "available": "可用 (available)",
    "failed": "失败 (failed)",
    "error": "错误 (Error)",
    "rejected": "已拒绝 (rejected)",
    "denied": "已拒绝 (denied)",
    "cancelled": "已取消 (cancelled)",
    "inactive": "未生效 (inactive)",
    "unavailable": "不可用 (unavailable)",
    "not found": "未找到 (not found)",
    "blocked": "已阻塞 (blocked)",
    "skipped": "已跳过 (skipped)",
}


def translate(language: UiLanguage, key: str, **values: object) -> str:
    """Translate a fixed UI key and format only its explicit placeholders."""
    lang = normalize_language(language)
    value = INSPECTOR_TEXT.get(lang, {}).get(key)
    if value is None:
        value = INSPECTOR_TEXT["en"].get(key, key)
    return value.format(**values) if values else value


def translate_status(language: UiLanguage, status: str) -> str:
    """Translate a canonical status while retaining its English meaning."""
    if normalize_language(language) == "en":
        return status
    return STATUS_ZH.get(status.lower(), status)
