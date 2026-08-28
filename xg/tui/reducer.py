"""Translate Agent/Plan events to UI state.

No terminal, network, database, or Textual operation belongs in this module.
That makes event ordering and stale-event handling straightforward to test.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from xg.agent.plan import PlanEvent
from xg.agent.react import AgentEvent
from xg.llm.types import Usage
from xg.tui.state import (
    PlanInspectorSnapshot,
    PlanTaskSnapshot,
    SafetyInspectorSnapshot,
    SessionInspectorSnapshot,
    TuiState,
    TranscriptItem,
)


def _copy(state: TuiState) -> TuiState:
    return replace(
        state,
        transcript=[replace(item) for item in state.transcript],
        plan_tasks=dict(state.plan_tasks),
        inspector=replace(
            state.inspector,
            session=replace(state.inspector.session),
            plan=replace(state.inspector.plan),
            memory=replace(state.inspector.memory),
            safety=replace(state.inspector.safety),
        ),
    )


def _append(state: TuiState, item: TranscriptItem) -> None:
    state.transcript.append(item)


def _remove_progress(state: TuiState, turn_id: str) -> None:
    """Remove local waiting indicators without touching Agent history."""
    state.transcript[:] = [
        item for item in state.transcript
        if not (item.kind == "progress" and item.turn_id == turn_id)
    ]


def _update_progress(state: TuiState, turn_id: str, text: str) -> None:
    """Update a local waiting indicator without creating a transcript item."""
    if not text:
        return
    for item in reversed(state.transcript):
        if item.kind == "progress" and item.turn_id == turn_id:
            item.progress_kind = "context"
            item.text = text
            return


def _trace_id(turn_id: str, trace_id: str | None) -> str:
    return trace_id or turn_id


def _ratio(value: int, limit: int) -> float:
    return value / limit if limit > 0 else 0.0


def _valid_usage(usage: Usage | None) -> bool:
    if usage is None:
        return False
    values = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    return all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for value in values
    )


def _update_context_usage(out: TuiState, event: AgentEvent) -> None:
    old = out.inspector.usage
    estimated = max(0, event.estimated_prompt_tokens or 0)
    window = event.context_window if event.context_window is not None else old.context_window
    limit = event.request_token_limit if event.request_token_limit is not None else old.request_token_limit
    compacted = event.compaction_before is not None and event.compaction_after is not None
    usage = replace(
        old,
        estimated_prompt_tokens=estimated,
        context_window=max(0, window or 0),
        request_token_limit=max(0, limit or 0),
        window_ratio=_ratio(estimated, window or 0),
        budget_usage_ratio=_ratio(estimated, limit or 0),
        usage_source="estimated",
        compaction_count=old.compaction_count + (1 if compacted else 0),
        last_compaction_before=(event.compaction_before or 0) if compacted else old.last_compaction_before,
        last_compaction_after=(event.compaction_after or 0) if compacted else old.last_compaction_after,
    )
    out.inspector = replace(
        out.inspector,
        usage=usage,
        context_tokens=estimated,
        context_window=max(0, window or 0),
        session=replace(out.inspector.session, status=_phase_status(out.phase)),
    )


def _update_provider_usage(out: TuiState, usage_value: Usage | None) -> None:
    if not _valid_usage(usage_value):
        return
    assert usage_value is not None
    old = out.inspector.usage
    usage = replace(
        old,
        last_prompt_tokens=usage_value.prompt_tokens,
        last_completion_tokens=usage_value.completion_tokens,
        last_total_tokens=usage_value.total_tokens,
        session_prompt_tokens=old.session_prompt_tokens + usage_value.prompt_tokens,
        session_completion_tokens=old.session_completion_tokens + usage_value.completion_tokens,
        session_total_tokens=old.session_total_tokens + usage_value.total_tokens,
        usage_source="provider",
    )
    out.inspector = replace(
        out.inspector,
        usage=usage,
        session=replace(out.inspector.session, status=_phase_status(out.phase)),
    )


def _phase_status(phase: str) -> str:
    return {
        "idle": "Idle",
        "running": "Working",
        "awaiting_approval": "Waiting approval",
        "awaiting_plan_review": "Plan review",
        "error": "Error",
    }.get(phase, phase)


def _plan_snapshot(
    plan,
    *,
    status: str | None = None,
    current_round: int | None = None,
    total_rounds: int | None = None,
    previous: PlanInspectorSnapshot | None = None,
) -> PlanInspectorSnapshot:
    tasks = tuple(
        PlanTaskSnapshot(id=task.id, title=task.title, status=task.status)
        for task in (plan.tasks if plan is not None else ())
    )
    done = sum(task.status == "done" for task in tasks)
    failures = sum(task.status == "failed" for task in tasks)
    old = previous or PlanInspectorSnapshot()
    return PlanInspectorSnapshot(
        goal=plan.goal if plan is not None else old.goal,
        status=status if status is not None else old.status,
        current_round=current_round if current_round is not None else old.current_round,
        total_rounds=total_rounds if total_rounds is not None else old.total_rounds,
        completed_tasks=done,
        total_tasks=len(tasks),
        failure_count=failures,
        tasks=tasks or old.tasks,
    )


def _set_safety_decision(out: TuiState, event: AgentEvent) -> None:
    decision = event.decision
    if decision is None:
        return
    status = event.text or ("approved" if decision.allow else "rejected")
    out.inspector = replace(
        out.inspector,
        safety=replace(
            out.inspector.safety,
            approval_status=status,
            current_tool="",
            current_level="",
            last_decision=status,
            last_reason=decision.reason,
            last_rejection=(decision.reason if not decision.allow else out.inspector.safety.last_rejection),
        ),
    )


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
    _remove_progress(out, turn_id)
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
    if kind in {
        "thinking", "content", "tool_call", "tool_result", "approval",
        "error", "context_overflow", "budget_exceeded", "step_limit", "done",
    }:
        _remove_progress(out, turn_id)
    if event.estimated_prompt_tokens is not None:
        _update_context_usage(out, event)
    if kind == "context_usage":
        return out
    if kind == "usage":
        _update_provider_usage(out, event.usage)
        return out
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
        if not result.ok:
            out.inspector = replace(
                out.inspector,
                safety=replace(
                    out.inspector.safety,
                    last_rejection=result.error or result.output or "tool_failed",
                ),
            )
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
        _set_safety_decision(out, event)
        return out
    if kind in ("context_compacted", "context_warning"):
        if kind == "context_compacted":
            _update_progress(out, turn_id, "正在整理上下文")
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
        _update_provider_usage(out, event.usage)
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
    if kind in {"plan_generated", "plan_done", "cancelled", "plan_failed"}:
        _remove_progress(out, turn_id)
    if kind == "plan_generated" and event.plan:
        if event.estimated_prompt_tokens is not None:
            _update_context_usage(out, AgentEvent(
                kind="context_usage",
                estimated_prompt_tokens=event.estimated_prompt_tokens,
                request_token_limit=event.request_token_limit,
                context_window=event.context_window,
                compaction_before=event.compaction_before,
                compaction_after=event.compaction_after,
            ))
        _update_provider_usage(out, event.usage)
        out.pending_plan = event.plan
        out.phase = "awaiting_plan_review"
        out.inspector = replace(
            out.inspector,
            plan_status="review",
            plan=_plan_snapshot(
                event.plan,
                status="review",
                current_round=0,
                total_rounds=len(event.plan.batches),
                previous=out.inspector.plan,
            ),
            session=replace(out.inspector.session, status="Plan review"),
        )
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
        out.inspector = replace(
            out.inspector,
            plan_status="running",
            plan=replace(out.inspector.plan, status="running"),
            session=replace(out.inspector.session, status="Working"),
        )
        return out
    if kind == "batch_started":
        out.inspector = replace(
            out.inspector,
            batch=event.message,
            plan=_plan_snapshot(
                event.plan,
                status="running",
                current_round=(
                    event.plan.batches.index(event.batch) + 1
                    if event.plan is not None and event.batch in event.plan.batches else out.inspector.plan.current_round
                ),
                total_rounds=len(event.plan.batches) if event.plan is not None else out.inspector.plan.total_rounds,
                previous=out.inspector.plan,
            ),
        )
        out.phase = "running"
        return out
    if kind == "subtask_started" and event.task:
        out.plan_tasks[event.task.id] = event.task.status
        plan = event.plan
        tasks = list(out.inspector.plan.tasks)
        for index, task in enumerate(tasks):
            if task.id == event.task.id:
                tasks[index] = PlanTaskSnapshot(task.id, task.title, event.task.status)
                break
        out.inspector = replace(
            out.inspector,
            plan=replace(
                out.inspector.plan,
                goal=plan.goal if plan is not None else out.inspector.plan.goal,
                tasks=tuple(tasks),
                total_tasks=len(tasks),
                completed_tasks=sum(task.status == "done" for task in tasks),
                failure_count=sum(task.status == "failed" for task in tasks),
            ),
        )
        return out
    if kind == "subtask_event" and event.agent_event:
        task_trace = f"{turn_id}:{event.task.id}" if event.task else turn_id
        return reduce_agent_event(out, event.agent_event, turn_id, task_trace)
    if kind == "planner_usage" and event.agent_event:
        return reduce_agent_event(out, event.agent_event, turn_id, f"{turn_id}:planner")
    if kind in ("subtask_done", "subtask_failed") and event.task:
        out.plan_tasks[event.task.id] = event.task.status
        task_trace = f"{turn_id}:{event.task.id}"
        _collapse_trace(out, task_trace, status="failed" if kind == "subtask_failed" else "done")
        for item in out.transcript:
            if item.trace_id == task_trace and item.kind == "assistant":
                item.collapsible = True
                if item.user_collapsed is None:
                    item.collapsed = True
        tasks = [
            PlanTaskSnapshot(task.id, task.title, event.task.status)
            if task.id == event.task.id else task
            for task in out.inspector.plan.tasks
        ]
        out.inspector = replace(
            out.inspector,
            plan=replace(
                out.inspector.plan,
                tasks=tuple(tasks),
                completed_tasks=sum(task.status == "done" for task in tasks),
                failure_count=sum(task.status == "failed" for task in tasks),
            ),
        )
        return out
    if kind in ("cancelled", "plan_done"):
        _collapse_turn(out, turn_id, status="cancelled" if kind == "cancelled" else "done")
        out.phase = "idle"
        out.pending_plan = None
        final_status = "done" if kind == "plan_done" else "cancelled"
        plan = _plan_snapshot(
            event.plan,
            status=final_status,
            previous=out.inspector.plan,
        )
        out.inspector = replace(
            out.inspector,
            plan_status=final_status,
            plan=plan,
            session=replace(out.inspector.session, status="Idle"),
        )
        out.notification = event.message
        out.notification_level = "info"
        return out
    if kind == "plan_failed":
        _collapse_turn(out, turn_id, status="failed")
        out.phase = "error"
        out.pending_plan = None
        out.inspector = replace(
            out.inspector,
            plan_status="failed",
            plan=_plan_snapshot(event.plan, status="failed", previous=out.inspector.plan),
            session=replace(out.inspector.session, status="Error"),
        )
        out.notification = event.message
        out.notification_level = "error"
        _append(out, TranscriptItem(id=f"plan-error-{len(out.transcript)}", kind="error", text=event.message, turn_id=turn_id))
        return out
    return out


def reduce_event(state: TuiState, event: AgentEvent | PlanEvent, turn_id: str | None = None) -> TuiState:
    if isinstance(event, AgentEvent):
        return reduce_agent_event(state, event, turn_id)
    return reduce_plan_event(state, event, turn_id)
