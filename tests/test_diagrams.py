from __future__ import annotations

from rich.console import Console

from xg.tui.diagrams import parse_flowchart, render_flowchart, split_mermaid_blocks
from xg.tui.renderables import render_item
from xg.tui.state import TranscriptItem


def test_split_mermaid_requires_a_complete_fence() -> None:
    parts = split_mermaid_blocks("before\n```mermaid\nflowchart TD\nA --> B\n``\nafter")
    assert len(parts) == 1
    assert parts[0][1] is None


def test_parser_supports_shapes_directions_and_labels() -> None:
    model = parse_flowchart(
        """graph LR
        start((开始)) --> check{有效?}
        check -->|是| run(执行)
        check -- 否 --> stop[结束]
        """
    )
    assert model.direction == "LR"
    assert [(node.id, node.shape) for node in model.nodes] == [
        ("start", "circle"), ("check", "diamond"), ("run", "round"), ("stop", "rect")
    ]
    assert [edge.label for edge in model.edges] == ["", "是", "否"]


def test_renderer_is_stable_and_has_ascii_fallback() -> None:
    model = parse_flowchart("flowchart TD\nA[开始] --> B[完成]")
    first = render_flowchart(model, width=80)
    second = render_flowchart(model, width=80)
    assert first == second
    assert first.mode == "unicode"
    assert "开始" in first.text and "完成" in first.text
    ascii_result = render_flowchart(model, width=80, unicode=False)
    assert ascii_result.mode == "ascii"
    assert "+" in ascii_result.text


def test_renderer_degrades_to_structured_text_when_too_narrow() -> None:
    model = parse_flowchart("flowchart TD\nA[这是一个足够长的节点标签] --> B[另一个足够长的节点标签]")
    result = render_flowchart(model, width=20)
    assert result.mode == "structured"
    assert "连线" in result.text


def test_assistant_mermaid_is_rendered_as_a_card_and_source_can_be_shown() -> None:
    item = TranscriptItem(
        id="assistant-1",
        kind="assistant",
        text="说明\n\n```mermaid\nflowchart LR\nA[开始] --> B[完成]\n```",
        diagram_source_visible=True,
    )
    console = Console(width=100, record=True)
    console.print(render_item(item))
    output = console.export_text()
    assert "Flowchart" in output
    assert "开始" in output and "完成" in output
    assert "```mermaid" in output
