from __future__ import annotations

from xg.agent.react import AgentEvent
from xg.llm.types import Usage
from xg.tui.reducer import reduce_agent_event
from xg.tui.state import TuiState
from xg.tui.widgets.header import HeaderBar
from xg.tui.widgets.inspector import InspectorPanel


def test_reducer_tracks_context_ratios_and_provider_session_usage() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(
            kind="context_usage",
            estimated_prompt_tokens=8_000,
            request_token_limit=80_000,
            context_window=100_000,
        ),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.window_ratio == 0.08
    assert usage.budget_usage_ratio == 0.1
    assert state.inspector.context_tokens == 8_000
    assert state.inspector.context_window == 100_000

    state = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(100, 20, 120)),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.last_total_tokens == 120
    assert usage.session_prompt_tokens == 100
    assert usage.session_completion_tokens == 20
    assert usage.session_total_tokens == 120
    assert usage.usage_source == "provider"


def test_reducer_ignores_invalid_and_stale_usage() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(10, 2, 12)),
        "turn-1",
    )
    stale = reduce_agent_event(
        state,
        AgentEvent(kind="done", usage=Usage(100, 100, 200)),
        "turn-old",
    )
    assert stale.inspector.usage.session_total_tokens == 12

    invalid = reduce_agent_event(
        state,
        AgentEvent(kind="usage", usage=Usage(-1, 2, 1)),
        "turn-1",
    )
    assert invalid.inspector.usage.session_total_tokens == 12


def test_context_compaction_is_structured() -> None:
    state = TuiState(active_turn_id="turn-1", phase="running")
    state = reduce_agent_event(
        state,
        AgentEvent(
            kind="context_usage",
            estimated_prompt_tokens=7_900,
            request_token_limit=102_400,
            context_window=128_000,
            compaction_before=18_240,
            compaction_after=7_900,
        ),
        "turn-1",
    )
    usage = state.inspector.usage
    assert usage.compaction_count == 1
    assert (usage.last_compaction_before, usage.last_compaction_after) == (18_240, 7_900)


def test_usage_widgets_show_unavailable_until_data_arrives() -> None:
    state = TuiState()
    header = HeaderBar()
    inspector = InspectorPanel()
    header.update_state(state)
    inspector.update_state(state)
    assert "Context -/-" in str(header.render())
    assert "source           unavailable" in str(inspector.render())
