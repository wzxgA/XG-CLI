"""Textual fullscreen UI integration.

Core execution code is intentionally kept outside this package.  Importing
``xg.tui`` therefore remains safe in environments where the optional UI
dependency has not been installed yet.
"""

from xg.tui.state import InspectorState, TranscriptItem, TuiState, UsageSnapshot

__all__ = ["InspectorState", "TranscriptItem", "TuiState", "UsageSnapshot"]
