"""Prompt Toolkit adapter backed by the shared XG input history."""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.history import History

from xg.input_history.store import InputHistory


class PromptToolkitHistory(History):
    """Use ``InputHistory`` without creating a second persistence file.

    The inline loop toggles ``recording_enabled`` only around its top-level
    prompt.  Approval and plan prompts share the PromptSession but therefore
    cannot accidentally enter ordinary task history.
    """

    def __init__(self, store: InputHistory) -> None:
        super().__init__()
        self.store = store
        self.recording_enabled = False

    def load_history_strings(self) -> Iterable[str]:
        yield from (entry.text for entry in reversed(self.store.entries()))

    def append_string(self, string: str) -> None:
        if self.recording_enabled:
            self.store.add(string)
        self._loaded = True
        self._loaded_strings = [entry.text for entry in reversed(self.store.entries())]

    def store_string(self, string: str) -> None:
        # ``append_string`` owns storage and applies the XG privacy policy.
        return None
