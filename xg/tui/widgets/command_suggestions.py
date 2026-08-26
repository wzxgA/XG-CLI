"""Local slash-command suggestions for the Composer."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList

from xg.cli.commands import SlashCommandSpec, filter_slash_commands
from xg.tui.messages import CommandSuggestionSelected


class CommandSuggestions(OptionList):
    """A non-focusable, mouse-selectable list above the Composer."""

    can_focus = False

    def __init__(self) -> None:
        super().__init__(id="command-suggestions", markup=False)
        self._visible_specs: tuple[SlashCommandSpec, ...] = ()
        self.display = False

    @property
    def visible_specs(self) -> tuple[SlashCommandSpec, ...]:
        return self._visible_specs

    @property
    def is_open(self) -> bool:
        return self.display and bool(self._visible_specs)

    def update_query(self, query: str) -> None:
        """Refresh candidates for a top-level slash-command token."""

        specs = filter_slash_commands(query)
        self._visible_specs = specs
        if not specs:
            self.set_options(())
            self.display = False
            return

        self.set_options(self._render_spec(spec) for spec in specs)
        self.highlighted = 0
        self.display = True

    def close(self) -> None:
        """Hide suggestions without changing the Composer value."""

        self.display = False

    def move_selection(self, direction: int) -> None:
        if not self._visible_specs:
            return
        current = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (current + direction) % len(self._visible_specs)
        self.scroll_to_highlight()

    def selected_command(self) -> str | None:
        if self.highlighted is None or not self._visible_specs:
            return None
        return self._visible_specs[self.highlighted].name

    @staticmethod
    def _render_spec(spec: SlashCommandSpec) -> Text:
        text = Text()
        text.append(spec.usage, style="bold")
        text.append("  ")
        text.append(spec.description, style="dim")
        if spec.aliases:
            text.append(f"  （别名：{'、'.join(spec.aliases)}）", style="dim")
        return text

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Turn a mouse click into a Composer fill operation."""

        if 0 <= event.option_index < len(self._visible_specs):
            event.stop()
            self.post_message(
                CommandSuggestionSelected(self._visible_specs[event.option_index].name)
            )
