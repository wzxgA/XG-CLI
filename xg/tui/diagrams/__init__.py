"""Dependency-free Mermaid flowchart support for the TUI."""

from xg.tui.diagrams.markdown import MermaidBlock, split_mermaid_blocks
from xg.tui.diagrams.layout import FlowchartLayout, LayoutNode, layout_flowchart
from xg.tui.diagrams.model import FlowchartEdge, FlowchartModel, FlowchartNode
from xg.tui.diagrams.parser import FlowchartParseError, parse_flowchart
from xg.tui.diagrams.renderer import DiagramRender, render_flowchart

__all__ = [
    "FlowchartEdge",
    "FlowchartLayout",
    "FlowchartModel",
    "FlowchartNode",
    "DiagramRender",
    "FlowchartParseError",
    "LayoutNode",
    "MermaidBlock",
    "parse_flowchart",
    "layout_flowchart",
    "render_flowchart",
    "split_mermaid_blocks",
]
