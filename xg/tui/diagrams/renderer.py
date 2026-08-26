"""Unicode/ASCII text rendering for a bounded flowchart layout."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len

from xg.tui.diagrams.layout import FlowchartLayout, layout_flowchart
from xg.tui.diagrams.model import FlowchartModel, FlowchartNode


@dataclass(frozen=True)
class DiagramRender:
    text: str
    mode: str = "unicode"
    warnings: tuple[str, ...] = ()
    width: int = 0
    height: int = 0


_UNICODE = {
    "h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "tee_down": "┬", "tee_up": "┴", "arrow": "▼", "arrow_right": "▶",
}
_ASCII = {
    "h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "tee_down": "+", "tee_up": "+", "arrow": "v", "arrow_right": ">",
}


def _fit(value: str, width: int) -> str:
    if width <= 1:
        return ""
    value = value.replace("\n", " ").strip()
    if cell_len(value) <= width:
        return value
    result = ""
    used = 0
    for char in value:
        size = cell_len(char)
        if used + size > max(0, width - 1):
            break
        result += char
        used += size
    return result + "…"


def _node_width(node: FlowchartNode, *, unicode: bool) -> int:
    # Two spaces of padding plus a little room for shape markers.  The shape
    # itself remains an approximation in a monospace terminal.
    return max(7, cell_len(_fit(node.label, 38)) + 4)


def _box(node: FlowchartNode, width: int, *, unicode: bool) -> list[str]:
    chars = _UNICODE if unicode else _ASCII
    label = _fit(node.label, width - 4)
    padding = max(0, width - 2 - cell_len(label))
    left = padding // 2
    right = padding - left
    top = chars["tl"] + chars["h"] * (width - 2) + chars["tr"]
    middle = chars["v"] + " " * left + label + " " * right + chars["v"]
    bottom = chars["bl"] + chars["h"] * (width - 2) + chars["br"]
    if node.shape == "round":
        if unicode:
            top = "╭" + "─" * (width - 2) + "╮"
            bottom = "╰" + "─" * (width - 2) + "╯"
        else:
            top = "/" + "-" * (width - 2) + "\\"
            bottom = "\\" + "-" * (width - 2) + "/"
    elif node.shape == "circle":
        if unicode:
            top = "╭" + "─" * (width - 2) + "╮"
            bottom = "╰" + "─" * (width - 2) + "╯"
        else:
            top = "(" + "-" * (width - 2) + ")"
            bottom = "(" + "-" * (width - 2) + ")"
    elif node.shape == "diamond":
        # A compact diamond-like marker keeps the graph readable at narrow
        # widths while avoiding a large, fragile multi-line polygon.
        top = "<" + "-" * (width - 2) + ">" if not unicode else "◇" + "─" * (width - 2) + "◇"
        bottom = top
    return [top, middle, bottom]


def _blank(width: int, height: int) -> list[list[str]]:
    return [[" "] * width for _ in range(height)]


_CONTINUATION = ""


def _put_text(canvas: list[list[str]], x: int, y: int, value: str) -> None:
    """Write text using terminal cells rather than Python character indexes."""
    if not (0 <= y < len(canvas)):
        return
    for char in value:
        char_width = cell_len(char)
        if char_width <= 0:
            if x > 0 and x <= len(canvas[y]) and canvas[y][x - 1] not in (" ", _CONTINUATION):
                canvas[y][x - 1] += char
            continue
        if x >= len(canvas[y]):
            break
        if x >= 0:
            canvas[y][x] = char
            for offset in range(1, char_width):
                if x + offset >= len(canvas[y]):
                    break
                canvas[y][x + offset] = _CONTINUATION
        x += char_width


def _put(canvas: list[list[str]], x: int, y: int, value: str) -> None:
    """Write one-cell diagram content without corrupting wide characters."""
    if 0 <= y < len(canvas) and 0 <= x < len(canvas[y]):
        if canvas[y][x] == _CONTINUATION:
            return
        canvas[y][x] = value


def _draw_horizontal(canvas: list[list[str]], y: int, x1: int, x2: int, char: str) -> None:
    if not (0 <= y < len(canvas)):
        return
    start, end = sorted((x1, x2))
    for x in range(start, end + 1):
        _put(canvas, x, y, char)


def _canvas_text(canvas: list[list[str]]) -> str:
    lines = []
    for row in canvas:
        # A wide character already contributes its full terminal width.  Its
        # continuation cells must not become literal spaces in the output,
        # otherwise ``中文`` would be serialized as ``中 文``.
        lines.append("".join(cell for cell in row if cell != _CONTINUATION).rstrip())
    return "\n".join(lines).rstrip()


def _text_dimensions(text: str) -> tuple[int, int]:
    lines = text.splitlines() or [""]
    return max((cell_len(line) for line in lines), default=0), len(lines)


def _render_vertical(model: FlowchartModel, layout: FlowchartLayout, *, width: int, unicode: bool) -> str:
    node_by_id = {node.id: node for node in model.nodes}
    gap_x = 5
    gap_y = 2
    widths = {node.id: _node_width(node, unicode=unicode) for node in model.nodes}
    layer_widths = [sum(widths[n] for n in layer) + gap_x * max(0, len(layer) - 1) for layer in layout.ranks]
    canvas_width = max(layer_widths or [1])
    if canvas_width > width:
        raise ValueError("flowchart width exceeded")
    box_h = 3
    layer_y = [rank * (box_h + gap_y) for rank in range(len(layout.ranks))]
    canvas = _blank(canvas_width, max(1, len(layout.ranks) * (box_h + gap_y) - gap_y))
    positions: dict[str, tuple[int, int]] = {}
    for rank, layer in enumerate(layout.ranks):
        current_x = (canvas_width - layer_widths[rank]) // 2
        for node_id in layer:
            node_width = widths[node_id]
            y = layer_y[rank]
            for row, line in enumerate(_box(node_by_id[node_id], node_width, unicode=unicode)):
                _put_text(canvas, current_x, y + row, line)
            positions[node_id] = (current_x, y)
            current_x += node_width + gap_x

    chars = _UNICODE if unicode else _ASCII
    for edge in model.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        source_w = widths[edge.source]
        target_w = widths[edge.target]
        start_x, end_x = sx + source_w // 2, tx + target_w // 2
        start_y, end_y = sy + box_h, ty - 1
        if end_y < start_y:
            continue  # backward/cyclic edge: do not overwrite another box
        line_char = "." if edge.style == "dotted" else "=" if edge.style == "thick" else chars["v"]
        if start_x == end_x:
            for y in range(start_y, end_y + 1):
                _put(canvas, start_x, y, line_char)
        else:
            mid_y = start_y + max(0, (end_y - start_y) // 2)
            for y in range(start_y, mid_y + 1):
                _put(canvas, start_x, y, line_char)
            _draw_horizontal(canvas, mid_y, start_x, end_x, chars["h"] if line_char != "." else ".")
            for y in range(mid_y, end_y + 1):
                _put(canvas, end_x, y, line_char)
        _put(canvas, end_x, end_y, chars["arrow"])
        if edge.label:
            label = _fit(edge.label, max(3, abs(end_x - start_x) - 2))
            if label and end_x != start_x:
                label_x = min(start_x, end_x) + max(1, (abs(end_x - start_x) - cell_len(label)) // 2)
                _put_text(canvas, label_x, mid_y, label)
    return _canvas_text(canvas)


def _render_horizontal(model: FlowchartModel, layout: FlowchartLayout, *, width: int, unicode: bool) -> str:
    node_by_id = {node.id: node for node in model.nodes}
    gap_x, gap_y = 7, 2
    widths = {node.id: _node_width(node, unicode=unicode) for node in model.nodes}
    layer_widths = [max((widths[n] for n in layer), default=7) for layer in layout.ranks]
    canvas_width = sum(layer_widths) + gap_x * max(0, len(layer_widths) - 1)
    if canvas_width > width:
        raise ValueError("flowchart width exceeded")
    layer_heights = [len(layer) * 3 + max(0, len(layer) - 1) * gap_y for layer in layout.ranks]
    canvas_height = max(layer_heights or [3])
    canvas = _blank(canvas_width, canvas_height)
    positions: dict[str, tuple[int, int]] = {}
    x = 0
    for rank, layer in enumerate(layout.ranks):
        layer_height = layer_heights[rank]
        y = (canvas_height - layer_height) // 2
        for node_id in layer:
            node_w = widths[node_id]
            node_x = x + (layer_widths[rank] - node_w) // 2
            for row, line in enumerate(_box(node_by_id[node_id], node_w, unicode=unicode)):
                _put_text(canvas, node_x, y + row, line)
            positions[node_id] = (node_x, y)
            y += 3 + gap_y
        x += layer_widths[rank] + gap_x

    chars = _UNICODE if unicode else _ASCII
    for edge in model.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        start_x, end_x = sx + widths[edge.source], tx - 1
        start_y, end_y = sy + 1, ty + 1
        if end_x < start_x:
            continue
        line_char = "." if edge.style == "dotted" else "=" if edge.style == "thick" else chars["h"]
        if start_y == end_y:
            _draw_horizontal(canvas, start_y, start_x, end_x, line_char)
        else:
            mid_x = start_x + max(0, (end_x - start_x) // 2)
            for x_pos in range(start_x, mid_x + 1):
                _put(canvas, x_pos, start_y, line_char)
            for y_pos in range(min(start_y, end_y), max(start_y, end_y) + 1):
                _put(canvas, mid_x, y_pos, chars["v"] if line_char != "." else ".")
            for x_pos in range(mid_x, end_x + 1):
                _put(canvas, x_pos, end_y, line_char)
        _put(canvas, end_x, end_y, chars["arrow_right"])
        if edge.label:
            label = _fit(edge.label, max(3, end_x - start_x - 2))
            _put_text(canvas, start_x + 1, start_y, label)
    return _canvas_text(canvas)


def _structured(model: FlowchartModel) -> str:
    lines = [f"Flowchart {model.direction}"]
    lines.extend(f"节点: {node.id}「{node.label}」" for node in model.nodes)
    lines.extend(
        f"连线: {edge.source} -> {edge.target}" + (f"「{edge.label}」" if edge.label else "")
        for edge in model.edges
    )
    return "\n".join(lines)


def render_flowchart(
    model: FlowchartModel,
    *,
    width: int = 120,
    unicode: bool = True,
    max_width: int = 160,
    max_height: int = 120,
    rank_by_node: dict[str, int] | None = None,
) -> DiagramRender:
    """Render a model, following the documented Unicode → ASCII → text order."""
    width = max(1, min(width, max_width))
    layout = layout_flowchart(model, rank_by_node=rank_by_node)
    warnings = list(model.warnings)
    renderers = [(unicode, "unicode"), (False, "ascii")] if unicode else [(False, "ascii")]
    for use_unicode, mode in renderers:
        try:
            text = _render_horizontal(model, layout, width=width, unicode=use_unicode) if model.direction in ("LR", "RL") else _render_vertical(model, layout, width=width, unicode=use_unicode)
            if text.count("\n") + 1 <= max_height:
                if layout.cyclic:
                    warnings.append("检测到循环边，已使用有限布局")
                render_width, render_height = _text_dimensions(text)
                return DiagramRender(
                    text=text,
                    mode=mode,
                    warnings=tuple(warnings),
                    width=render_width,
                    height=render_height,
                )
        except ValueError:
            continue
    warnings.append("图表超出当前终端宽度或高度，已降级为结构化文本")
    text = _structured(model)
    render_width, render_height = _text_dimensions(text)
    return DiagramRender(
        text=text,
        mode="structured",
        warnings=tuple(warnings),
        width=render_width,
        height=render_height,
    )
