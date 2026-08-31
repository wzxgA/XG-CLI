"""Pure unit tests for the P0 completion module (xg.cli.completion)."""

from __future__ import annotations

from xg.cli.completion import (
    CompletionCandidate,
    complete_command_token,
    parse_completion_line,
    replace_span,
    sort_candidates,
    tokenize,
)


# --- tokenize ---


def test_tokenize_splits_on_whitespace_with_offsets():
    tokens = tokenize("/model deepseek/deepseek-chat")
    assert [(t.text, t.start, t.end) for t in tokens] == [
        ("/model", 0, 6),
        ("deepseek/deepseek-chat", 7, 29),
    ]


def test_tokenize_keeps_quoted_segments_together():
    tokens = tokenize('/web search "Python 3.13 新特性"')
    assert [(t.text, t.start, t.end) for t in tokens] == [
        ("/web", 0, 4),
        ("search", 5, 11),
        ('"Python 3.13 新特性"', 12, 29),
    ]


def test_tokenize_tolerates_unterminated_quote():
    tokens = tokenize('/web search "未闭合')
    assert [(t.text, t.start, t.end) for t in tokens] == [
        ("/web", 0, 4),
        ("search", 5, 11),
        ('"未闭合', 12, 16),
    ]


def test_tokenize_empty_and_leading_whitespace():
    assert tokenize("") == []
    assert [(t.text, t.start) for t in tokenize("   /plan")] == [("/plan", 3)]


# --- parse_completion_line ---


def test_parse_detects_command_context():
    ctx = parse_completion_line("/model deepseek")
    assert ctx.is_command is True
    assert ctx.command == "/model"
    assert ctx.command_token_start == 0
    assert ctx.command_token_end == 6


def test_parse_command_token_offsets_account_for_leading_whitespace():
    ctx = parse_completion_line("  /team resume")
    assert ctx.is_command is True
    assert ctx.command == "/team"
    assert ctx.command_token_start == 2
    assert ctx.command_token_end == 7


def test_parse_non_command_is_false():
    assert parse_completion_line("实现登录模块").is_command is False
    assert parse_completion_line("").is_command is False


def test_parse_current_token_at_cursor():
    ctx = parse_completion_line("/team res", cursor_position=6)
    assert ctx.current_token == "res"
    assert ctx.current_token_start == 6
    assert ctx.current_token_end == 9


def test_parse_cursor_clamped_to_input_length():
    ctx = parse_completion_line("/team resume", cursor_position=999)
    assert ctx.cursor_position == len("/team resume")


def test_parse_cursor_after_trailing_space_has_no_current_token():
    ctx = parse_completion_line("/team resume ", cursor_position=len("/team resume "))
    assert ctx.current_token == ""
    # Insertion point becomes the cursor position itself.
    assert ctx.current_token_start == len("/team resume ")
    assert ctx.current_token_end == len("/team resume ")


# --- complete_command_token ---


def test_complete_replaces_leading_token_preserving_trailing_text():
    value, cursor = complete_command_token("/team res 实现模块", "/team resume")
    assert value == "/team resume res 实现模块"
    assert cursor == len(value)


def test_complete_preserves_leading_whitespace():
    value, cursor = complete_command_token("  /m 之后", "/model")
    assert value == "  /model 之后"
    assert cursor == len(value)


def test_complete_non_command_is_unchanged():
    value, cursor = complete_command_token("普通文本", "/model")
    assert value == "普通文本"
    assert cursor == len(value)


# --- replace_span ---


def test_replace_span_mid_token_preserves_suffix():
    new, cur = replace_span("xg/au extra", 0, 5, "xg/auth/")
    assert new == "xg/auth/ extra"
    assert cur == len("xg/auth/")


# --- sort_candidates / CompletionCandidate ---


def test_candidate_sort_floats_exact_match_first():
    candidates = [
        CompletionCandidate("/cancel", "/cancel", detail="取消任务"),
        CompletionCandidate("/config", "/config", detail="配置"),
        CompletionCandidate("/clear", "/clear", detail="清屏"),
    ]
    ordered = sort_candidates(candidates, exact_match="/cancel")
    assert [c.insert_text for c in ordered] == ["/cancel", "/clear", "/config"]


def test_candidate_sort_respects_sort_key_then_label():
    candidates = [
        CompletionCandidate("b", "b", detail="", sort_key=(1, "z")),
        CompletionCandidate("a", "a", detail="", sort_key=(0, "")),
    ]
    ordered = sort_candidates(candidates)
    assert [c.insert_text for c in ordered] == ["a", "b"]