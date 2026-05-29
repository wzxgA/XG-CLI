"""审计日志：统一 JSONL 记录 + 敏感字段脱敏。

记录位置：<项目根>/.xg/audit.log
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_SENSITIVE_KEY_RE = re.compile(r"^(api_key|apikey|token|authorization|password|secret|private_key)$", re.I)
_BEARER_RE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)
_REDACTED = "***"


def redact(value: Any) -> Any:
    """递归脱敏：敏感键名的值、文本中的 Bearer token（保留键名，脱敏值）。"""
    if isinstance(value, dict):
        return {
            str(k): _REDACTED if _SENSITIVE_KEY_RE.match(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _BEARER_RE.sub(r"\1***", value)
    return value


class AuditLogger:
    def __init__(self, log_path: str | Path, session_id: str | None = None) -> None:
        self.log_path = Path(log_path)
        self.session_id = session_id or uuid4().hex[:8]

    def record(self, action: str, **fields: Any) -> None:
        entry: dict[str, Any] = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "session": self.session_id,
            "action": action,
        }
        entry.update({k: redact(v) for k, v in fields.items()})
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def tool_call(self, tool: str, args: dict, ok: bool, duration_ms: int, approved: bool = True) -> None:
        self.record(
            "tool_call",
            tool=tool,
            args=args,
            approved=approved,
            ok=ok,
            duration_ms=duration_ms,
        )

    def approval(self, tool: str, args: dict, decision: str, reason: str = "") -> None:
        self.record("approval", tool=tool, args=args, decision=decision, reason=reason)

    def blocked(self, reason: str, **detail: Any) -> None:
        self.record("blocked", reason=reason, **detail)
