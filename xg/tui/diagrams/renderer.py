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


def _wrap(value: str, width: int) -> list[str]:
    """Wrap text by terminal cells without dropping CJK or emoji characters."""
    if width <= 0:
        return [""]
    lines: list[str] = []
    for raw_line in value.replace("\r", "").split("\n"):
        current = ""
        used = 0
        for char in raw_line:
            char_width = cell_len(char)
            if char_width > 0 and current and used + char_width > width:
                lines.append(current)
                current = ""
                used = 0
            current += char
            used += char_width
        lines.append(current)
    return lines or [""]


def _node_width(node: FlowchartNode, *, unicode: bool) -> int:
    # Keep nodes bounded so long titles wrap instead of forcing the whole
    # diagram beyond the terminal width.
    longest_line = max((cell_len(line) for line in node.label.splitlines()), default=0)
    return max(7, min(38, longest_line) + 4)


def _box(node: FlowchartNode, width: int, *, unicode: bool) -> list[str]:
    chars = _UNICODE if unicode else _ASCII
    top = chars["tl"] + chars["h"] * (width - 2) + chars["tr"]
    bottom = chars["bl"] + chars["h"] * (width - 2) + chars["br"]
    middle: list[str] = []
    for label in _wrap(node.label, width - 4):
        padding = max(0, width - 2 - cell_len(label))
        left = padding // 2
        right = padding - left
        middle.append(chars["v"] + " " * left + label + " " * right + chars["v"])
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
    return [top, *middle, bottom]


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


def _merge_edge_char(existing: str, value: str, *, unicode: bool) -> str:
    """Merge crossing connector characters without destroying either path."""
    if existing in (" ", _CONTINUATION):
        return value
    # An arrow is the semantic endpoint of an edge.  It must win over the
    # vertical segment that was drawn immediately before it.
    if value in {"▼", "▶", "v", ">"}:
        return value
    if existing in {"▼", "▶", "v", ">"}:
        return existing
    if existing == value:
        return existing
    if not unicode:
        return "+"
    if {existing, value} <= {"─", "│"}:
        return "┼"
    if existing in {"─", "│", "┼"} and value in {"─", "│", "┼"}:
        return "┼"
    return existing


def _put_edge(
    canvas: list[list[str]],
    x: int,
    y: int,
    value: str,
    *,
    unicode: bool,
    node_regions: list[tuple[int, int, int, int, int]],
) -> None:
    """Write a connector while protecting every node rectangle."""
    if not (0 <= y < len(canvas) and 0 <= x < len(canvas[y])):
        return
    if any(left <= x < left + node_width and top <= y < top + node_height
           for left, top, node_width, node_height, _ in node_regions):
        return
    existing = canvas[y][x]
    if existing == _CONTINUATION:
        return
    canvas[y][x] = _merge_edge_char(existing, value, unicode=unicode)


def _draw_edge_horizontal(
    canvas: list[list[str]],
    y: int,
    x1: int,
    x2: int,
    char: str,
    *,
    unicode: bool,
    node_regions: list[tuple[int, int, int, int, int]],
) -> None:
    start, end = sorted((x1, x2))
    for x in range(start, end + 1):
        _put_edge(canvas, x, y, char, unicode=unicode, node_regions=node_regions)


def _draw_edge_vertical(
    canvas: list[list[str]],
    x: int,
    y1: int,
    y2: int,
    char: str,
    *,
    unicode: bool,
    node_regions: list[tuple[int, int, int, int, int]],
) -> None:
    start, end = sorted((y1, y2))
    for y in range(start, end + 1):
        _put_edge(canvas, x, y, char, unicode=unicode, node_regions=node_regions)


def _safe_vertical_lanes(
    canvas_width: int,
    *,
    source_rank: int,
    target_rank: int,
    node_regions: list[tuple[int, int, int, int, int]],
) -> list[int]:
    """Return x columns that stay outside nodes crossed by a long edge."""
    blocked: set[int] = set()
    for rank in range(source_rank + 1, target_rank):
        for left, _, node_width, _, node_rank in node_regions:
            if node_rank == rank:
                blocked.update(range(left, left + node_width))
    return [x for x in range(canvas_width) if x not in blocked]


def _choose_vertical_lane(
    candidates: list[int],
    source_x: int,
    target_x: int,
) -> int | None:
    if not candidates:
        return None
    # Prefer a lane close to both endpoints, which keeps short branches
    # compact while still allowing long edges to pass around intermediate
    # ranks safely.
    return min(candidates, key=lambda x: (abs(x - source_x) + abs(x - target_x), x))


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
    widths = {node.id: _node_width(node, unicode=unicode) for node in model.nodes}
    boxes = {node.id: _box(node, widths[node.id], unicode=unicode) for node in model.nodes}
    heights = {node.id: len(boxes[node.id]) for node in model.nodes}
    layer_widths = [sum(widths[n] for n in layer) + gap_x * max(0, len(layer) - 1) for layer in layout.ranks]
    rank_by_node = {
        node_id: rank
        for rank, layer in enumerate(layout.ranks)
        for node_id in layer
    }
    layer_heights = [max((heights[node_id] for node_id in layer), default=3) for layer in layout.ranks]
    adjacent_edges: dict[int, list[tuple[int, object]]] = {}
    for edge_index, edge in enumerate(model.edges):
        source_rank = rank_by_node.get(edge.source)
        target_rank = rank_by_node.get(edge.target)
        if source_rank is not None and target_rank == source_rank + 1:
            adjacent_edges.setdefault(source_rank, []).append((edge_index, edge))

    # Each direct dependency gets its own horizontal routing row.  Without
    # this extra space, all edges between two rounds collapse into one bus.
    layer_gaps = {
        rank: max(2, len(edges) + 1)
        for rank, edges in adjacent_edges.items()
    }
    layer_y = [0]
    for rank in range(max(0, len(layout.ranks) - 1)):
        layer_y.append(layer_y[-1] + layer_heights[rank] + layer_gaps.get(rank, 2))

    # Reserve a small outer channel for edges which must pass around one or
    # more intermediate ranks.  Multiple channels are selected below so
    # long edges remain individually traceable.
    long_edge_count = any(
        edge.source in rank_by_node
        and edge.target in rank_by_node
        and rank_by_node[edge.target] - rank_by_node[edge.source] > 1
        for edge in model.edges
    )
    routing_margin = 2 if long_edge_count else 0
    canvas_width = max(layer_widths or [1]) + routing_margin * 2
    if canvas_width > width:
        raise ValueError("flowchart width exceeded")
    canvas_height = (layer_y[-1] + layer_heights[-1]) if layer_y else 1
    canvas = _blank(canvas_width, max(1, canvas_height))
    positions: dict[str, tuple[int, int]] = {}
    node_regions: list[tuple[int, int, int, int, int]] = []
    for rank, layer in enumerate(layout.ranks):
        current_x = (canvas_width - layer_widths[rank]) // 2
        for node_id in layer:
            node_width = widths[node_id]
            y = layer_y[rank]
            for row, line in enumerate(boxes[node_id]):
                _put_text(canvas, current_x, y + row, line)
            positions[node_id] = (current_x, y)
            node_regions.append((current_x, y, node_width, heights[node_id], rank))
            current_x += node_width + gap_x

    chars = _UNICODE if unicode else _ASCII
    edge_index_by_identity = {id(edge): edge_index for edge_index, edge in enumerate(model.edges)}
    incoming_edges: dict[str, list[int]] = {}
    for edge_index, edge in enumerate(model.edges):
        if edge.source in positions and edge.target in positions:
            incoming_edges.setdefault(edge.target, []).append(edge_index)
    target_ports: dict[int, int] = {}
    for target, edge_indices in incoming_edges.items():
        target_x, _ = positions[target]
        target_width = widths[target]
        inner_width = max(1, target_width - 2)
        ordered_edges = sorted(
            edge_indices,
            key=lambda edge_index: (
                model.edges[edge_index].source,
                model.edges[edge_index].target,
                edge_index,
            ),
        )
        for order, edge_index in enumerate(ordered_edges):
            if len(ordered_edges) == 1:
                offset = inner_width // 2
            else:
                offset = round(order * (inner_width - 1) / (len(ordered_edges) - 1))
            target_ports[edge_index] = target_x + 1 + offset

    edge_rows: dict[int, dict[int, int]] = {}
    for rank, edges in adjacent_edges.items():
        start = layer_y[rank] + layer_heights[rank]
        end = layer_y[rank + 1] - 1
        rows = list(range(start, end + 1))
        edge_rows[rank] = {
            edge_index: rows[min(order, len(rows) - 1)]
            for order, (edge_index, _) in enumerate(edges)
        }
    used_long_lanes: set[int] = set()
    for edge in sorted(
        model.edges,
        key=lambda item: (
            rank_by_node.get(item.target, 0) - rank_by_node.get(item.source, 0),
            item.source,
            item.target,
        ),
        reverse=True,
    ):
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        source_w = widths[edge.source]
        target_w = widths[edge.target]
        source_h = heights[edge.source]
        edge_index = edge_index_by_identity[id(edge)]
        start_x = sx + source_w // 2
        end_x = target_ports.get(edge_index, tx + target_w // 2)
        start_y, end_y = sy + source_h, ty - 1
        if end_y < start_y:
            continue  # backward/cyclic edge: do not overwrite another box
        horizontal_char = "." if edge.style == "dotted" else "=" if edge.style == "thick" else chars["h"]
        vertical_char = "." if edge.style == "dotted" else "=" if edge.style == "thick" else chars["v"]
        source_rank = rank_by_node[edge.source]
        target_rank = rank_by_node[edge.target]
        if target_rank - source_rank <= 1:
            # Route each adjacent edge through its own row in the gap.
            route_y = edge_rows.get(source_rank, {}).get(
                edge_index,
                layer_y[source_rank] + layer_heights[source_rank],
            )
            _draw_edge_vertical(canvas, start_x, start_y, route_y, vertical_char,
                                unicode=unicode, node_regions=node_regions)
            _draw_edge_horizontal(canvas, route_y, start_x, end_x, horizontal_char,
                                  unicode=unicode, node_regions=node_regions)
            _draw_edge_vertical(
                canvas, end_x, route_y, end_y, vertical_char,
                unicode=unicode,
                node_regions=node_regions,
            )
        else:
            lanes = _safe_vertical_lanes(
                canvas_width,
                source_rank=source_rank,
                target_rank=target_rank,
                node_regions=node_regions,
            )
            unused_lanes = [lane for lane in lanes if lane not in used_long_lanes]
            lane = _choose_vertical_lane(unused_lanes or lanes, start_x, end_x)
            if lane is None:
                # This should only occur for an invalid layout with no spare
                # column. Keep the edge out of node cells rather than drawing
                # a misleading line through another task.
                continue
            used_long_lanes.add(lane)
            _draw_edge_horizontal(
                canvas, start_y, start_x, lane, horizontal_char,
                unicode=unicode,
                node_regions=node_regions,
            )
            _draw_edge_vertical(
                canvas, lane, start_y, end_y, vertical_char,
                unicode=unicode,
                node_regions=node_regions,
            )
            _draw_edge_horizontal(
                canvas, end_y, lane, end_x, horizontal_char,
                unicode=unicode,
                node_regions=node_regions,
            )
        _put_edge(
            canvas, end_x, end_y, chars["arrow"],
            unicode=unicode,
            node_regions=node_regions,
        )
        if edge.label:
            label = _fit(edge.label, max(3, abs(end_x - start_x) - 2))
            if label and end_x != start_x and target_rank - source_rank <= 1:
                route_y = edge_rows.get(source_rank, {}).get(edge_index, start_y)
                label_x = min(start_x, end_x) + max(1, (abs(end_x - start_x) - cell_len(label)) // 2)
                _put_text(canvas, label_x, route_y, label)
    return _canvas_text(canvas)


def _render_horizontal(model: FlowchartModel, layout: FlowchartLayout, *, width: int, unicode: bool) -> str:
    node_by_id = {node.id: node for node in model.nodes}
    gap_x, gap_y = 7, 2
    widths = {node.id: _node_width(node, unicode=unicode) for node in model.nodes}
    boxes = {node.id: _box(node, widths[node.id], unicode=unicode) for node in model.nodes}
    heights = {node.id: len(boxes[node.id]) for node in model.nodes}
    layer_widths = [max((widths[n] for n in layer), default=7) for layer in layout.ranks]
    canvas_width = sum(layer_widths) + gap_x * max(0, len(layer_widths) - 1)
    if canvas_width > width:
        raise ValueError("flowchart width exceeded")
    layer_heights = [sum(heights[node_id] for node_id in layer) + max(0, len(layer) - 1) * gap_y for layer in layout.ranks]
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
            for row, line in enumerate(boxes[node_id]):
                _put_text(canvas, node_x, y + row, line)
            positions[node_id] = (node_x, y)
            y += heights[node_id] + gap_y
        x += layer_widths[rank] + gap_x

    chars = _UNICODE if unicode else _ASCII
    for edge in model.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        start_x, end_x = sx + widths[edge.source], tx - 1
        start_y, end_y = sy + heights[edge.source] // 2, ty + heights[edge.target] // 2
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
