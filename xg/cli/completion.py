"""Pure command-completion parsing and token replacement for XG.

This is the P0 slice of ``enhancement-v2/07``: it moves the parsing and
text-replacement logic out of the Textual ``Composer`` widget so it can be
unit-tested without launching a UI.  User-visible behaviour is intentionally
unchanged in this phase.

Later phases (P1-P3) layer subcommand, option and dynamic-value completion on
top of the data model introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CompletionKind = Literal["command", "subcommand", "argument", "option", "value", "path"]


@dataclass(frozen=True)
class Token:
    """One whitespace-separated token with absolute offsets in the input."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class CompletionCandidate:
    """A candidate: what text to insert, where, and how to present it.

    A candidate only describes text insertion.  It never carries tool calls,
    file writes, command execution or any other side effect.
    """

    label: str
    insert_text: str
    detail: str = ""
    kind: CompletionKind = "value"
    sort_key: tuple[int, str] = (0, "")


@dataclass(frozen=True)
class CompletionContext:
    """Parsed view of a composer line, independent of any UI widget."""

    raw: str
    cursor_position: int
    is_command: bool = False
    command: str = ""
    command_token_start: int = 0
    command_token_end: int = 0
    tokens: tuple[Token, ...] = ()
    current_token: str = ""
    current_token_start: int = 0
    current_token_end: int = 0


def tokenize(raw: str) -> list[Token]:
    """Split on whitespace, keeping quoted segments together.

    Defensive by design: an unterminated quote never raises; the remaining
    text is returned as one trailing token.
    """

    tokens: list[Token] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index].isspace():
            index += 1
        if index >= length:
            break
        start = index
        quote: str | None = None
        while index < length:
            char = raw[index]
            if quote is not None:
                index += 1
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                index += 1
                continue
            if char.isspace():
                break
            index += 1
        tokens.append(Token(raw[start:index], start, index))
    return tokens


def _clamp(value: int, length: int) -> int:
    return max(0, min(length, value))


def _current_token(tokens: tuple[Token, ...], cursor: int) -> Token | None:
    """Return the token under the cursor.

    The cursor sitting exactly on a token's end boundary belongs to that
    token so that completing right after a word still targets it.
    """

    chosen: Token | None = None
    for token in tokens:
        if token.start <= cursor <= token.end:
            chosen = token
    return chosen


def parse_completion_line(raw: str, cursor_position: int | None = None) -> CompletionContext:
    """Parse a composer line into a non-throwing CompletionContext.

    A line is a command context when its first token starts with ``/``.
    Partial or malformed input never raises; it simply yields an incomplete
    context that callers can ignore.
    """

    cursor = _clamp(len(raw) if cursor_position is None else cursor_position, len(raw))
    tokens = tuple(tokenize(raw))
    is_command = False
    command = ""
    command_start = 0
    command_end = 0
    if tokens and tokens[0].text.startswith("/"):
        is_command = True
        command = tokens[0].text.lower()
        command_start = tokens[0].start
        command_end = tokens[0].end

    active = _current_token(tokens, cursor)
    return CompletionContext(
        raw=raw,
        cursor_position=cursor,
        is_command=is_command,
        command=command,
        command_token_start=command_start,
        command_token_end=command_end,
        tokens=tokens,
        current_token=active.text if active else "",
        current_token_start=active.start if active else cursor,
        current_token_end=active.end if active else cursor,
    )


def replace_span(raw: str, start: int, end: int, replacement: str) -> tuple[str, int]:
    """Return ``(new_text, new_cursor)`` with ``raw[start:end]`` replaced.

    The returned cursor points to the end of the inserted text.
    """

    new = raw[:start] + replacement + raw[end:]
    return new, start + len(replacement)


def complete_command_token(raw: str, command: str) -> tuple[str, int]:
    """Replace the leading slash command token with ``command``.

    Reproduces the exact behaviour the App/Composer used to implement
    inline: leading whitespace and any trailing text after the command are
    preserved, and the final cursor moves to the end of the resulting line.
    Non-command input is returned unchanged.
    """

    ctx = parse_completion_line(raw)
    if not ctx.is_command:
        return raw, len(raw)
    new_text, _ = replace_span(
        raw, ctx.command_token_start, ctx.command_token_end, command
    )
    return new_text, len(new_text)


def sort_candidates(
    candidates: list[CompletionCandidate], *, exact_match: str | None = None
) -> list[CompletionCandidate]:
    """Stable sort by sort_key then label; exact matches float to the front.

    ``exact_match`` is the lowercase text that, when it equals a candidate's
    insert label, ranks that candidate first (mirrors the exact-alias-first
    ordering of top-level command filtering).
    """

    if exact_match is not None:
        exact_match = exact_match.lower()
    return sorted(
        candidates,
        key=lambda cand: (
            0
            if exact_match is not None and cand.insert_text.lower() == exact_match
            else 1,
            cand.sort_key[0],
            cand.sort_key[1] or cand.label,
            cand.label,
        ),
    )