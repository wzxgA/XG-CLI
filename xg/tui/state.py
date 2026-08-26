"""Pure state types consumed by the Textual widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TranscriptKind = Literal[
    "user", "assistant", "tool_call", "tool_result", "approval",
    "context", "plan", "error", "system",
]
TuiPhase = Literal[
    "idle", "running", "awaiting_approval", "awaiting_plan_review", "error",
]


@dataclass
class TranscriptItem:
    id: str
    kind: TranscriptKind
    text: str = ""
    tool_name: str = ""
    tool_args: str = ""
    tool_call_id: str = ""
    tool_ok: bool | None = None
    collapsed: bool = True
    streaming: bool = False
    turn_id: str = ""


@dataclass
class InspectorState:
    provider: str = ""
    model: str = ""
    context_tokens: int = 0
    context_window: int = 0
    memory_count: int = 0
    hitl_enabled: bool = True
    plan_status: str = "idle"
    batch: str = ""


@dataclass
class TuiState:
    phase: TuiPhase = "idle"
    transcript: list[TranscriptItem] = field(default_factory=list)
    inspector: InspectorState = field(default_factory=InspectorState)
    active_turn_id: str = ""
    active_input: str = ""
    pending_approval: Any | None = None
    pending_plan: Any | None = None
    pending_confirmation: ConfirmationRequest | None = None
    notification: str = ""
    notification_level: Literal["info", "warning", "error"] = "info"
    plan_tasks: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRequest:
    tool_name: str
    level: str
    args: dict
    turn_id: str = ""


@dataclass(frozen=True)
class ConfirmationRequest:
    kind: str
    title: str
    body: str
    payload: object | None = None
