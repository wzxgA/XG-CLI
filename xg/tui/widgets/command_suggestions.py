"""Local slash-command suggestions for the Composer."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList

from xg.cli.commands import SLASH_COMMANDS, SlashCommandSpec
from xg.cli.completion import (
    CompletionCandidate,
    CompletionProviderRegistry,
    completion_candidates,
    dynamic_candidates,
)
from xg.tui.messages import CommandSuggestionSelected


class CommandSuggestions(OptionList):
    """A non-focusable, mouse-selectable list above the Composer.

    The list shows layered candidates (command / subcommand / static option /
    dynamic value) in front of a thin horizontal rule... The displayed text
    keeps the candidate's insert_text separate, so help text never leaks into
    the value.
    """

    can_focus = False

    def __init__(self) -> None:
        super().__init__(id="command-suggestions", markup=False)
        self._visible_candidates: list[CompletionCandidate] = []
        self._revision = 0
        self._registry: CompletionProviderRegistry | None = None
        self.display = False

    def set_dynamic_registry(self, registry: CompletionProviderRegistry | None) -> None:
        """Attach the runtime provider registry for dynamic value candidates."""
        self._registry = registry

    @property
    def visible_candidates(self) -> list[CompletionCandidate]:
        return self._visible_candidates

    @property
    def visible_specs(self) -> tuple[SlashCommandSpec, ...]:
        """Top-level command specs (backward-compatible accessor).

        Only meaningful while the candidate list is a pure command list; empty
        otherwise. New callers should use ``visible_candidates`` instead.
        """
        return tuple(
            spec
            for cand in self._visible_candidates
            if cand.kind == "command"
            for spec in SLASH_COMMANDS
            if spec.name == cand.insert_text
        )

    @property
    def is_open(self) -> bool:
        return self.display and bool(self._visible_candidates)

    def update_query(self, query: str, cursor_position: int | None = None) -> None:
        """Refresh layered candidates (static + dynamic) for the input line.

        Static candidates come first, then dynamic value candidates (the
        providers are synchronous and read only in-memory agent state), then
        the list is re-rendered. Dynamic updates use a per-query revision so
        a stale refresh from an older input never clobbers a newer one.
        """
        revision = self._revision + 1
        self._revision = revision
        static = completion_candidates(query, cursor_position)
        dynamic = (
            dynamic_candidates(query, cursor_position, self._registry)
            if self._registry is not None
            else []
        )
        candidates = list(static) + dynamic
        # Defensive: if a newer update_query() bumped the revision before we
        # applied this result, drop it. Synchronous path never races in
        # practice, but the guard keeps a stale refresh from winning.
        if self._revision != revision:
            return
        self._visible_candidates = candidates
        if not candidates:
            self.set_options(())
            self.display = False
            return

        self.set_options(self._render_candidate(cand) for cand in candidates)
        self.highlighted = 0
        self.display = True

    def close(self) -> None:
        """Hide suggestions without changing the Composer value."""

        self.display = False

    def move_selection(self, direction: int) -> None:
        if not self._visible_candidates:
            return
        current = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (current + direction) % len(self._visible_candidates)
        self.scroll_to_highlight()

    def selected_candidate(self) -> CompletionCandidate | None:
        if self.highlighted is None or not self._visible_candidates:
            return None
        return self._visible_candidates[self.highlighted]

    @staticmethod
    def _render_candidate(candidate: CompletionCandidate) -> Text:
        text = Text()
        text.append(candidate.label, style="bold")
        if candidate.detail:
            text.append("  ")
            text.append(candidate.detail, style="dim")
        return text

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Turn a mouse click into a Composer fill operation."""

        if 0 <= event.option_index < len(self._visible_candidates):
            event.stop()
            self.post_message(
                CommandSuggestionSelected(self._visible_candidates[event.option_index])
            )