"""Translate Agent/Plan events to UI state.

No terminal, network, database, or Textual operation belongs in this module.
That makes event ordering and stale-event handling straightforward to test.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from xg.agent.plan import PlanEvent
from xg.agent.react import AgentEvent
from xg.tui.state import TuiState, TranscriptItem


def _copy(state: TuiState) -> TuiState:
    return replace(
        state,
        transcript=[replace(item) for item in state.transcript],
        plan_tasks=dict(state.plan_tasks),
        inspector=replace(state.inspector),
    )


def _append(state: TuiState, item: TranscriptItem) -> None:
    state.transcript.append(item)


def _trace_id(turn_id: str, trace_id: str | None) -> str:
    return trace_id or turn_id


def _finish_streaming_text(state: TuiState, trace_id: str) -> None:
    for item in reversed(state.transcript):
        if item.trace_id != trace_id:
            continue
        if item.kind in ("assistant", "thinking") and item.streaming:
            item.streaming = False
            if item.kind == "thinking":
                item.status = "done"
                if item.user_collapsed is None:
                    item.collapsed = True
            return


def _reclassify_intermediate_assistant(state: TuiState, turn_id: str, trace_id: str) -> None:
    """Treat pre-tool plain text as thinking when no explicit event exists."""
    for item in reversed(state.transcript):
        if item.turn_id != turn_id or item.trace_id != trace_id:
            continue
        if item.kind != "assistant" or not item.text:
            return
        item.kind = "thinking"
        item.collapsible = True
        item.collapsed = True
        item.user_collapsed = None
        item.streaming = False
        item.status = "done"
        return


def _assistant(state: TuiState, turn_id: str, trace_id: str) -> TranscriptItem:
    if state.transcript and state.transcript[-1].kind == "assistant" and state.transcript[-1].streaming and state.transcript[-1].trace_id == trace_id:
        return state.transcript[-1]
    item = TranscriptItem(
        id=f"assistant-{len(state.transcript)}", kind="assistant", streaming=True,
        collapsed=False, turn_id=turn_id, trace_id=trace_id, status="streaming",
    )
    _append(state, item)
    return item


def _thinking(state: TuiState, turn_id: str, trace_id: str) -> TranscriptItem:
    if state.transcript and state.transcript[-1].kind == "thinking" and state.transcript[-1].streaming and state.transcript[-1].trace_id == trace_id:
        return state.transcript[-1]
    item = TranscriptItem(
        id=f"thinking-{len(state.transcript)}", kind="thinking", collapsible=True,
        collapsed=False, streaming=True, turn_id=turn_id, trace_id=trace_id,
        status="streaming",
    )
    _append(state, item)
    return item


def _collapse_trace(state: TuiState, trace_id: str, *, status: str = "done") -> None:
    for item in state.transcript:
        if item.trace_id != trace_id or not item.collapsible:
            continue
        item.streaming = False
        if item.user_collapsed is None:
            item.collapsed = True
        if item.status in ("streaming", "running"):
            item.status = status  # type: ignore[assignment]


def _collapse_turn(state: TuiState, turn_id: str, *, status: str = "done") -> None:
    for item in state.transcript:
        if item.trace_id == turn_id or item.trace_id.startswith(f"{turn_id}:"):
            _collapse_trace(state, item.trace_id, status=status)
            if item.kind == "assistant" and item.trace_id.startswith(f"{turn_id}:"):
                item.collapsible = True
                if item.user_collapsed is None:
                    item.collapsed = True


def finalize_trace(state: TuiState, turn_id: str, *, status: str = "cancelled") -> TuiState:
    """Finalize a turn when its worker is cancelled outside the event stream."""
    out = _copy(state)
    _collapse_turn(out, turn_id, status=status)
    return out


def reduce_agent_event(
    state: TuiState,
    event: AgentEvent,
    turn_id: str | None = None,
    trace_id: str | None = None,
) -> TuiState:
    """Return the next state for one ReAct event."""
    turn_id = turn_id or state.active_turn_id
    if state.active_turn_id and turn_id and state.active_turn_id != turn_id:
        return state
    out = _copy(state)
    trace_id = _trace_id(turn_id, trace_id)
    kind = event.kind
    if kind == "thinking":
        if not (out.transcript and out.transcript[-1].kind == "thinking" and out.transcript[-1].streaming and out.transcript[-1].trace_id == trace_id):
            _finish_streaming_text(out, trace_id)
        item = _thinking(out, turn_id, trace_id)
        item.text += event.text
        return out
    if kind == "content":
        if out.transcript and out.transcript[-1].kind == "thinking" and out.transcript[-1].trace_id == trace_id:
            _finish_streaming_text(out, trace_id)
        item = _assistant(out, turn_id, trace_id)
        item.text += event.text
        return out
    if kind == "tool_call" and event.tool_call:
        _finish_streaming_text(out, trace_id)
        _reclassify_intermediate_assistant(out, turn_id, trace_id)
        call = event.tool_call
        _append(out, TranscriptItem(
            id=call.id or f"tool-{len(out.transcript)}", kind="tool_call",
            tool_name=call.name, tool_args=call.arguments, tool_call_id=call.id,
            turn_id=turn_id, trace_id=trace_id, collapsible=True,
            collapsed=False, status="running",
        ))
        out.phase = "running"
        return out
    if kind == "tool_result" and event.tool_result:
        result = event.tool_result
        for item in reversed(out.transcript):
            if item.kind == "tool_call" and item.tool_call_id == result.tool_call_id:
                item.tool_ok = result.ok
                item.status = "success" if result.ok else "failed"
                if item.user_collapsed is None:
                    item.collapsed = True
                break
        _append(out, TranscriptItem(
            id=f"result-{result.tool_call_id or len(out.transcript)}", kind="tool_result",
            text=result.output or result.error, tool_name=result.name,
            tool_call_id=result.tool_call_id, tool_ok=result.ok, turn_id=turn_id,
            trace_id=trace_id, collapsible=True, collapsed=True,
            status="success" if result.ok else "failed", parent_call_id=result.tool_call_id,
        ))
        return out
    if kind == "approval":
        if event.tool_call:
            _append(out, TranscriptItem(
                id=f"approval-{event.tool_call.id or len(out.transcript)}", kind="approval",
                text=event.text, tool_name=event.tool_call.name,
                tool_args=event.tool_call.arguments, turn_id=turn_id, trace_id=trace_id,
                collapsible=True, collapsed=True, status="done",
            ))
        out.pending_approval = None
        out.phase = "running"
        return out
    if kind in ("context_compacted", "context_warning"):
        _append(out, TranscriptItem(id=f"context-{len(out.transcript)}", kind="context", text=event.text, turn_id=turn_id))
        out.notification = event.text
        out.notification_level = "warning" if kind == "context_warning" else "info"
        return out
    if kind in ("error", "context_overflow", "budget_exceeded"):
        text = event.text or "任务执行失败"
        _append(out, TranscriptItem(id=f"error-{len(out.transcript)}", kind="error", text=text, turn_id=turn_id))
        out.phase = "error"
        out.notification = text
        out.notification_level = "error"
        out.pending_approval = None
        _collapse_trace(out, trace_id, status="failed")
        return out
    if kind in ("step_limit", "done"):
        for item in reversed(out.transcript):
            if item.kind == "assistant" and item.trace_id == trace_id:
                item.streaming = False
                item.status = "done"
                break
        _collapse_trace(out, trace_id, status="done")
        out.phase = "idle"
        out.pending_approval = None
        if kind == "step_limit":
            out.notification = "已达到本轮步骤上限"
            out.notification_level = "warning"
        return out
    return out


def reduce_plan_event(state: TuiState, event: PlanEvent, turn_id: str | None = None) -> TuiState:
    """Return the next state for one Plan event, including nested agent events."""
    turn_id = turn_id or state.active_turn_id
    if state.active_turn_id and turn_id and state.active_turn_id != turn_id:
        return state
    out = _copy(state)
    kind = event.kind
    if kind == "plan_generated" and event.plan:
        out.pending_plan = event.plan
        out.phase = "awaiting_plan_review"
        out.inspector.plan_status = "review"
        out.plan_tasks = {task.id: task.status for task in event.plan.tasks}
        _append(out, TranscriptItem(
            id=f"plan-{len(out.transcript)}", kind="plan", text=event.plan.goal,
            plan=event.plan, plan_review=True, collapsed=True, turn_id=turn_id,
        ))
        return out
    if kind == "review":
        out.phase = "awaiting_plan_review"
        return out
    if kind in ("approved", "replanned"):
        for item in reversed(out.transcript):
            if item.kind == "plan" and item.turn_id == turn_id and (event.plan is None or item.plan is event.plan):
                item.plan_review = False
                break
        out.phase = "running"
        out.pending_plan = None
        out.inspector.plan_status = "running"
        return out
    if kind == "batch_started":
        out.inspector.batch = event.message
        out.phase = "running"
        return out
    if kind == "subtask_started" and event.task:
        out.plan_tasks[event.task.id] = event.task.status
        return out
    if kind == "subtask_event" and event.agent_event:
        task_trace = f"{turn_id}:{event.task.id}" if event.task else turn_id
        return reduce_agent_event(out, event.agent_event, turn_id, task_trace)
    if kind in ("subtask_done", "subtask_failed") and event.task:
        task_trace = f"{turn_id}:{event.task.id}"
        _collapse_trace(out, task_trace, status="failed" if kind == "subtask_failed" else "done")
        for item in out.transcript:
            if item.trace_id == task_trace and item.kind == "assistant":
                item.collapsible = True
                if item.user_collapsed is None:
                    item.collapsed = True
        return out
    if kind in ("cancelled", "plan_done"):
        _collapse_turn(out, turn_id, status="cancelled" if kind == "cancelled" else "done")
        out.phase = "idle"
        out.pending_plan = None
        out.inspector.plan_status = "done" if kind == "plan_done" else "cancelled"
        out.notification = event.message
        out.notification_level = "info"
        return out
    if kind == "plan_failed":
        _collapse_turn(out, turn_id, status="failed")
        out.phase = "error"
        out.pending_plan = None
        out.inspector.plan_status = "failed"
        out.notification = event.message
        out.notification_level = "error"
        _append(out, TranscriptItem(id=f"plan-error-{len(out.transcript)}", kind="error", text=event.message, turn_id=turn_id))
        return out
    return out


def reduce_event(state: TuiState, event: AgentEvent | PlanEvent, turn_id: str | None = None) -> TuiState:
    if isinstance(event, AgentEvent):
        return reduce_agent_event(state, event, turn_id)
    return reduce_plan_event(state, event, turn_id)
