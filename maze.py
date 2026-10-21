"""Data model for the multi-floor maze.

Coordinate system
-----------------
* A *cell* is a square identified by ``(x, y)`` where ``x`` grows to the right
  and ``y`` grows downwards (image coordinates).
* The maze is a stack of *layers*.  ``layer[0]`` is the bottom of the stack and
  ``layer[-1]`` the top.  Only above-ground floors exist, so the floor number of
  a layer is simply ``stack_index + 1`` -- the bottom layer is 1 层.
* ``floor`` is the user facing number, ``layer_index`` the 0-based stack
  position used by the renderer: ``floor == layer_index + 1``.

Connectivity
------------
Walls are *thin*: they live on the boundary between two orthogonally adjacent
cells of the same layer and are described by **two independent directions**:

* ``passable(a, b) is True``  -> you may walk from ``a`` into ``b``.
* ``passable(b, a) is True``  -> you may walk from ``b`` into ``a``.

That yields exactly the three states required by the specification:

===============  ==============  ==============================
a -> b           b -> a          rendering
===============  ==============  ==============================
True             True            nothing drawn (open passage)
False            False           solid line on the shared edge
True             False           dashed line + arrow head pointing b -> a
False            True            dashed line + arrow head pointing a -> b
===============  ==============  ==============================

The default for every pair of neighbours is *open in both directions*, so a
freshly created maze is an empty open field.

面积占比 (area ratio)
--------------------
Every layer stores an integer ``area_ratio`` in ``0..100`` (default 100).  For a
cell of ``size`` pixels it gives::

    inner = round(size * area_ratio / 100)      # at least 1 pixel
    inset = (size - inner) / 2

The four points ``(inset, inset)``, ``(inset, size - inset)``,
``(size - inset, inset)`` and ``(size - inset, size - inset)`` cut the cell into
nine blocks.  The floor is the **central block** plus one **edge block** towards
every neighbour that has a floor of its own *and* is not cut off by a solid wall:
an air neighbour gets no block, a *missing* neighbour (the border of the maze)
gets none either, and a walled edge gets none on either side.  A one-way edge
counts as connected, so its two floors still meet.  The four corner blocks
therefore always stay empty.

A cell's walls live on its four *inset lines* (the lines through those points).
Each of them is cut into three parts by two of the points:

* the **middle part** (as long as ``inner``) carries the solid wall or the
  one-way gate of that side; with an open edge nothing is drawn.  A solid wall
  sits on the inset line (the edge of the floor); a one-way gate sits on the
  *shared border* while the two floors meet there,
* the two **outer parts** are drawn when the *arm* of the perpendicular side
  exists (same condition as the edge block) -- they outline the empty corner
  blocks, which is why even a cell that is connected on all four sides still
  shows eight short segments.

Only cells with a floor draw anything at all: an air cell paints neither floor
nor walls, so an edge whose two cells are both air stays invisible.

``area_ratio == 100`` degenerates to the plain full-cell look: ``inset == 0``,
the outer parts vanish and the walls sit exactly on the shared edges.

颜色与不透明度 (colours and opacity)
------------------------------------
``object_color`` / ``floor_color`` / ``portal_color`` are stored as
``#RRGGBBAA``: the first six digits are the colour, the last two its opacity
(``FF`` fully visible, ``00`` invisible).  ``#RRGGBB`` and ``#RGB`` are accepted
as well and count as fully opaque, so files written before opacity existed keep
their old look.

Colours are read-only for the compositing rules: every layer paints itself with
the opacity of its own colours and the layers are stacked from the bottom of the
stack upwards.  How much of the lower floors shines through is therefore a
property of the *colours*, not of the distance to the active floor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

Cell = Tuple[int, int]

#: Cell kinds ------------------------------------------------------------
AIR = "air"        # 无内容的空气，透明
GROUND = "ground"  # 普通地面，白色底
PORTAL = "portal"  # 单向传送门，白色底 + 指向目的地中心的箭头
TEXT = "text"      # 含文本的单元，白色底 + 居中自适应文本
CELL_KINDS: Tuple[str, ...] = (AIR, GROUND, PORTAL, TEXT)

CELL_KIND_LABELS: Dict[str, str] = {
    AIR: "空气",
    GROUND: "地面",
    PORTAL: "单向传送门",
    TEXT: "文本",
}

#: Edge axes: 0 -> the neighbour is (x+1, y); 1 -> the neighbour is (x, y+1)
AXIS_X = 0
AXIS_Y = 1

FILE_VERSION = 1

#: Default appearance of a *new* layer.  Every layer stores its own values, so
#: the user can give each floor a different look from the toolbar.  Colours are
#: ``#RRGGBBAA``: the last two digits are the opacity of that colour.
DEFAULT_OBJECT_COLOR = "#000000FF"   # 黑
DEFAULT_FLOOR_COLOR = "#FFFFFFFF"    # 白
DEFAULT_PORTAL_COLOR = "#FF00FFFF"   # 品红
DEFAULT_WALL_WIDTH = 3
#: Percentage of a cell that the floor of a *new* layer covers.  100 fills the
#: whole cell; smaller values shrink the floor towards the cell centre and pull
#: the walls inwards (see the module docstring).
DEFAULT_AREA_RATIO = 100

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def normalise_color(value: Any, fallback: str) -> str:
    """Return ``value`` as an uppercase ``#RRGGBBAA`` string, or ``fallback``.

    The first six digits are the colour, the last two its opacity (``FF`` fully
    visible, ``00`` invisible).  ``#RGB`` and ``#RRGGBB`` are accepted as well
    and count as fully opaque; the leading ``#`` is optional.  Anything unusable
    falls back instead of raising, so a hand-edited file can never stop the
    editor from loading.  ``fallback`` is expected to be a valid colour already.
    """
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3 and all(ch in _HEX_DIGITS for ch in text):
        text = "".join(ch * 2 for ch in text)
    if len(text) == 6 and all(ch in _HEX_DIGITS for ch in text):
        text += "FF"
    if len(text) == 8 and all(ch in _HEX_DIGITS for ch in text):
        return "#" + text.upper()
    return fallback


def normalise_wall_width(value: Any, fallback: int = DEFAULT_WALL_WIDTH) -> int:
    """Clamp a wall width into ``1..64``, falling back when it is not a number."""
    try:
        return max(1, min(64, int(float(value))))
    except (TypeError, ValueError):
        return fallback


def normalise_area_ratio(value: Any, fallback: int = DEFAULT_AREA_RATIO) -> int:
    """Clamp an area ratio into ``0..100``, falling back when it is not a number."""
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


@dataclass
class CellData:
    """Contents of a single cell (walls are stored separately)."""

    kind: str = AIR
    text: str = ""
    #: Portal destination, same layer only.  ``None`` means "not set yet".
    portal: Optional[Cell] = None

    def copy(self) -> "CellData":
        return CellData(self.kind, self.text, self.portal)

    @property
    def is_empty(self) -> bool:
        return self.kind == AIR and not self.text and self.portal is None

    def to_json(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"kind": self.kind}
        if self.kind == TEXT:
            payload["text"] = self.text
        if self.kind == PORTAL and self.portal is not None:
            payload["portal"] = [int(self.portal[0]), int(self.portal[1])]
        return payload

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "CellData":
        kind = str(raw.get("kind", AIR))
        if kind not in CELL_KINDS:
            kind = AIR
        text = str(raw.get("text", ""))
        portal: Optional[Cell] = None
        raw_portal = raw.get("portal")
        if isinstance(raw_portal, (list, tuple)) and len(raw_portal) == 2:
            try:
                portal = (int(raw_portal[0]), int(raw_portal[1]))
            except (TypeError, ValueError):
                portal = None
        return cls(kind=kind, text=text, portal=portal)


def edge_key(a: Cell, b: Cell) -> Tuple[int, int, int]:
    """Canonical, undirected key for the edge shared by two adjacent cells.

    Args:
        a: first cell.
        b: second cell, orthogonally adjacent to ``a``.

    Returns:
        ``(cx, cy, axis)`` where ``(cx, cy)`` is the cell with the smaller
        coordinate along ``axis`` and ``axis`` is :data:`AXIS_X` when the two
        cells differ in ``x``, :data:`AXIS_Y` when they differ in ``y``.

    Raises:
        ValueError: if the cells are not orthogonally adjacent.
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if abs(dx) + abs(dy) != 1:
        raise ValueError(f"cells {a} and {b} are not orthogonally adjacent")
    if dx != 0:
        low = a if a[0] < b[0] else b
        return (low[0], low[1], AXIS_X)
    low = a if a[1] < b[1] else b
    return (low[0], low[1], AXIS_Y)


def edge_cells(key: Tuple[int, int, int]) -> Tuple[Cell, Cell]:
    """Inverse of :func:`edge_key`: the two cells of an edge, low one first."""
    cx, cy, axis = key
    if axis == AXIS_X:
        return (cx, cy), (cx + 1, cy)
    return (cx, cy), (cx, cy + 1)


class Layer:
    """One floor of the maze: a ``width x height`` grid of cells plus walls.

    Each layer also carries its own *look* -- object colour, floor colour,
    portal colour (all ``#RRGGBBAA``, the last two digits being the opacity),
    wall width and area ratio -- so different floors may be drawn differently.
    These five values are stored in the maze file.
    """

    def __init__(self, width: int, height: int, name: str = "",
                 object_color: str = DEFAULT_OBJECT_COLOR,
                 floor_color: str = DEFAULT_FLOOR_COLOR,
                 portal_color: str = DEFAULT_PORTAL_COLOR,
                 wall_width: int = DEFAULT_WALL_WIDTH,
                 area_ratio: int = DEFAULT_AREA_RATIO) -> None:
        self.width = int(width)
        self.height = int(height)
        self.name = name
        #: Colour (and opacity) of the walls, text and one-way arrows.
        self.object_color = normalise_color(object_color, DEFAULT_OBJECT_COLOR)
        #: Background colour (and opacity) of the cells of this layer.
        self.floor_color = normalise_color(floor_color, DEFAULT_FLOOR_COLOR)
        #: Colour (and opacity) of this layer's portal arrows.
        self.portal_color = normalise_color(portal_color, DEFAULT_PORTAL_COLOR)
        #: Width of the solid wall lines of this layer, in pixels.
        self.wall_width = normalise_wall_width(wall_width)
        #: Percentage of each cell covered by the floor of this layer (0..100).
        self.area_ratio = normalise_area_ratio(area_ratio)
        #: Sparse storage: only non-default cells are kept.
        self.cells: Dict[Cell, CellData] = {}
        #: Sparse storage: only edges that are *not* open both ways are kept.
        #: key -> [a_to_b, b_to_a]
        self.edges: Dict[Tuple[int, int, int], List[bool]] = {}

    # -- geometry --------------------------------------------------------
    def contains(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def iter_cells(self) -> Iterator[Cell]:
        for y in range(self.height):
            for x in range(self.width):
                yield (x, y)

    # -- cell contents ---------------------------------------------------
    def get_cell(self, x: int, y: int) -> CellData:
        """Cell contents; returns a throwaway default for out-of-bounds cells."""
        if not self.contains(x, y):
            return CellData()
        return self.cells.get((x, y), CellData())

    def set_cell(self, x: int, y: int, data: CellData) -> None:
        if not self.contains(x, y):
            return
        if data.is_empty:
            self.cells.pop((x, y), None)
        else:
            self.cells[(x, y)] = data

    def clear_cell(self, x: int, y: int) -> None:
        self.cells.pop((x, y), None)

    # -- connectivity ----------------------------------------------------
    def passable(self, a: Cell, b: Cell) -> bool:
        """``True`` when walking from ``a`` into ``b`` is allowed."""
        if a == b:
            return True
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
            return False  # not adjacent -> no direct connection
        key = edge_key(a, b)
        state = self.edges.get(key)
        if state is None:
            return True  # default: fully open
        low, high = edge_cells(key)
        return bool(state[0]) if a == low else bool(state[1])

    def edge_state(self, a: Cell, b: Cell) -> Tuple[bool, bool]:
        """``(a_to_b, b_to_a)`` for the shared edge of two adjacent cells."""
        key = edge_key(a, b)
        state = self.edges.get(key)
        if state is None:
            return True, True
        low, high = edge_cells(key)
        if a == low:
            return bool(state[0]), bool(state[1])
        return bool(state[1]), bool(state[0])

    def set_edge(self, a: Cell, b: Cell, a_to_b: bool, b_to_a: bool) -> None:
        """Set both directions of the edge between two adjacent cells."""
        key = edge_key(a, b)
        low, high = edge_cells(key)
        forward, backward = (a_to_b, b_to_a) if a == low else (b_to_a, a_to_b)
        if forward and backward:
            self.edges.pop(key, None)  # fully open is the default
        else:
            self.edges[key] = [bool(forward), bool(backward)]

    def set_edge_state(self, a: Cell, b: Cell, state: str) -> None:
        """Convenience wrapper: ``state`` is one of ``open``/``wall``/``oneway``."""
        if state == "open":
            self.set_edge(a, b, True, True)
        elif state == "wall":
            self.set_edge(a, b, False, False)
        elif state == "oneway":
            self.set_edge(a, b, True, False)
        else:
            raise ValueError(f"unknown edge state: {state!r}")

    def open_edges(self, x: int, y: int) -> Dict[str, bool]:
        """Directional passage flags around a cell (only in-bounds neighbours)."""
        result: Dict[str, bool] = {}
        for name, (dx, dy) in (("N", (0, -1)), ("E", (1, 0)), ("S", (0, 1)), ("W", (-1, 0))):
            nx, ny = x + dx, y + dy
            if self.contains(nx, ny):
                result[name] = self.passable((x, y), (nx, ny))
        return result

    def connected(self, a: Cell, b: Cell) -> bool:
        """``True`` when the wall between ``a`` and ``b`` lets either way through."""
        return self.passable(a, b) or self.passable(b, a)

    def region_cells(self, start: Cell, kind: str) -> List[Cell]:
        """Every cell of ``kind`` connected to ``start``.

        Steps are orthogonal only (diagonals do not connect) and a neighbour is
        only visited when the edge between the two cells is not solid, so a wall
        ends the region.  Returns an empty list when ``start`` does not hold
        ``kind`` (or lies outside the layer).
        """
        if not self.contains(*start) or self.get_cell(*start).kind != kind:
            return []
        found = [start]
        seen = {start}
        head = 0
        while head < len(found):
            x, y = found[head]
            head += 1
            for cell in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if cell in seen or not self.contains(*cell):
                    continue
                if self.get_cell(*cell).kind != kind:
                    continue
                if not self.connected((x, y), cell):
                    continue
                seen.add(cell)
                found.append(cell)
        return sorted(found)

    # -- bulk operations -------------------------------------------------
    def fill(self, kind: str, text: str = "") -> None:
        """Set every cell of the layer to one kind (does not touch walls)."""
        self.cells.clear()
        if kind != AIR:
            for cell in self.iter_cells():
                self.cells[cell] = CellData(kind=kind, text=text)

    def open_all(self) -> None:
        self.edges.clear()

    def wall_all(self) -> None:
        """Surround every cell with solid walls (outer border + inner edges)."""
        self.edges.clear()
        for y in range(self.height):
            for x in range(self.width):
                for nx, ny in ((x + 1, y), (x, y + 1)):
                    if self.contains(nx, ny):
                        self.edges[edge_key((x, y), (nx, ny))] = [False, False]

    def clear_all(self) -> None:
        self.cells.clear()
        self.edges.clear()

    def resize(self, width: int, height: int) -> None:
        """Resize the grid, dropping everything that falls outside."""
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.cells = {c: d for c, d in self.cells.items() if self.contains(*c)}
        self.edges = {
            k: v
            for k, v in self.edges.items()
            if self.contains(*edge_cells(k)[0]) and self.contains(*edge_cells(k)[1])
        }

    def copy(self) -> "Layer":
        clone = Layer(self.width, self.height, self.name,
                      self.object_color, self.floor_color,
                      self.portal_color, self.wall_width, self.area_ratio)
        clone.cells = {c: d.copy() for c, d in self.cells.items()}
        clone.edges = {k: list(v) for k, v in self.edges.items()}
        return clone

    # -- flood fill ------------------------------------------------------
    def reachable_from(self, start: Cell) -> set:
        """Set of cells reachable from ``start`` (directed, respects one-ways)."""
        if not self.contains(*start):
            return set()
        seen = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if not self.contains(nx, ny):
                    continue
                if (nx, ny) in seen:
                    continue
                if self.passable((x, y), (nx, ny)):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return seen

    # -- serialisation ---------------------------------------------------
    def to_json(self) -> Dict[str, Any]:
        cells = [
            [x, y, data.to_json()]
            for (x, y), data in sorted(self.cells.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ]
        edges = [
            [key[0], key[1], key[2], int(state[0]), int(state[1])]
            for key, state in sorted(self.edges.items())
        ]
        return {
            "name": self.name,
            "object_color": self.object_color,
            "floor_color": self.floor_color,
            "portal_color": self.portal_color,
            "wall_width": int(self.wall_width),
            "area_ratio": int(self.area_ratio),
            "cells": cells,
            "edges": edges,
        }

    @classmethod
    def from_json(cls, raw: Dict[str, Any], width: int, height: int,
                  fallback: Optional["Layer"] = None) -> "Layer":
        """Rebuild a layer; ``fallback`` supplies the look of older files.

        Files written before per-layer appearance existed only stored a palette
        in ``config.json``, which no longer exists: those layers keep the
        defaults, or the look of the first layer of the same file.
        """
        source = fallback
        layer = cls(
            width, height, str(raw.get("name", "")),
            raw.get("object_color", source.object_color if source else DEFAULT_OBJECT_COLOR),
            raw.get("floor_color", source.floor_color if source else DEFAULT_FLOOR_COLOR),
            raw.get("portal_color", source.portal_color if source else DEFAULT_PORTAL_COLOR),
            raw.get("wall_width", source.wall_width if source else DEFAULT_WALL_WIDTH),
            raw.get("area_ratio", source.area_ratio if source else DEFAULT_AREA_RATIO),
        )
        for entry in raw.get("cells", []) or []:
            try:
                x, y, payload = int(entry[0]), int(entry[1]), entry[2]
            except (TypeError, ValueError, IndexError):
                continue
            if layer.contains(x, y) and isinstance(payload, dict):
                layer.cells[(x, y)] = CellData.from_json(payload)
        for entry in raw.get("edges", []) or []:
            try:
                cx, cy, axis, forward, backward = (
                    int(entry[0]),
                    int(entry[1]),
                    int(entry[2]),
                    bool(int(entry[3])),
                    bool(int(entry[4])),
                )
            except (TypeError, ValueError, IndexError):
                continue
            key = (cx, cy, axis)
            low, high = edge_cells(key)
            if layer.contains(*low) and layer.contains(*high) and not (forward and backward):
                layer.edges[key] = [forward, backward]
        return layer


class Maze:
    """A stack of :class:`Layer` objects plus stack level metadata.

    Floor numbering
    ---------------
    Only above-ground floors exist: the floor number is a pure function of the
    stack position, the bottom layer being 1 层::

        floor_of(i) = i + 1

    Inserting a layer below therefore renumbers the whole stack, and inserting
    one above simply appends the next number.  The *look* of a floor (colours,
    wall width and area ratio) is stored per layer instead of being derived from
    the floor number.
    """

    def __init__(self, width: int = 16, height: int = 12, layers: int = 3) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        count = max(1, int(layers))
        self.layers: List[Layer] = [Layer(self.width, self.height) for _ in range(count)]
        #: Index of the layer currently being edited.
        self.active = 0

    # -- layers ----------------------------------------------------------
    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def start_floor(self) -> int:
        """Floor number of ``layers[0]`` (the bottom of the stack): always 1."""
        return 1

    def layer(self, index: int) -> Layer:
        return self.layers[index]

    def floor_of(self, index: int) -> int:
        """Floor number of the layer at stack ``index`` (bottom layer is 1)."""
        return int(index) + 1

    def index_of_floor(self, floor: int) -> int:
        """Stack index of the layer with floor number ``floor``."""
        return int(floor) - 1

    def add_layer(self, above: bool = True) -> int:
        """Insert an empty layer at the top (``above``) or bottom of the stack.

        Floor numbers are recomputed from the new stack height, so inserting
        below renumbers the floors that were already there.
        """
        if above:
            self.layers.append(Layer(self.width, self.height))
            return len(self.layers) - 1
        self.layers.insert(0, Layer(self.width, self.height))
        self.active += 1
        return 0

    def remove_layer(self, index: int) -> bool:
        """Delete a layer; refuses to delete the very last remaining one."""
        if len(self.layers) <= 1 or not 0 <= index < len(self.layers):
            return False
        del self.layers[index]
        self.active = max(0, min(self.active if self.active < index else self.active - 1,
                                 len(self.layers) - 1))
        return True

    def move_layer(self, index: int, delta: int) -> int:
        """Move a layer up/down inside the stack; returns its new index.

        Floor *numbers* are a property of the whole stack, so reordering layers
        keeps every floor number attached to the same grid contents.
        """
        target = index + delta
        if not (0 <= index < len(self.layers) and 0 <= target < len(self.layers)):
            return index
        layer = self.layers.pop(index)
        self.layers.insert(target, layer)
        if self.active == index:
            self.active = target
        elif index < self.active <= target:
            self.active -= 1
        elif target <= self.active < index:
            self.active += 1
        return target

    def active_layer(self) -> Layer:
        return self.layers[self.active]

    # -- grid size -------------------------------------------------------
    def resize(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        for layer in self.layers:
            layer.resize(self.width, self.height)

    # -- cell helpers on the active layer --------------------------------
    def contains(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get_cell(self, x: int, y: int) -> CellData:
        return self.active_layer().get_cell(x, y)

    def set_cell(self, x: int, y: int, data: CellData) -> None:
        self.active_layer().set_cell(x, y, data)

    def passable(self, a: Cell, b: Cell) -> bool:
        return self.active_layer().passable(a, b)

    # -- serialisation ---------------------------------------------------
    def to_json(self) -> Dict[str, Any]:
        return {
            "format": "maze-map-designer",
            "version": FILE_VERSION,
            "width": self.width,
            "height": self.height,
            "active": self.active,
            "layers": [layer.to_json() for layer in self.layers],
        }

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Maze":
        if not isinstance(raw, dict):
            raise ValueError("迷宫文件的根节点必须是一个 JSON 对象")
        width = max(1, int(raw.get("width", 16)))
        height = max(1, int(raw.get("height", 12)))
        raw_layers = raw.get("layers")
        if not isinstance(raw_layers, list) or not raw_layers:
            raise ValueError("迷宫文件缺少 layers 字段")
        # ``basement`` / ``lowest_floor`` from older files are ignored: every
        # maze is numbered from 1 层 at the bottom of the stack.
        maze = cls(width, height, len(raw_layers))
        # Older files carried no per-layer look; every layer then inherits the
        # look of the first one (which itself falls back to the defaults).
        maze.layers = []
        previous: Optional[Layer] = None
        for item in raw_layers:
            layer = Layer.from_json(item or {}, width, height, fallback=previous)
            maze.layers.append(layer)
            previous = layer
        maze.active = max(0, min(int(raw.get("active", 0)), len(maze.layers) - 1))
        return maze

    def save(self, path: str) -> str:
        """Write the maze to ``path`` as UTF-8 JSON."""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_json(), handle, indent=1, ensure_ascii=False)
            handle.write("\n")
        return path

    @classmethod
    def load(cls, path: str) -> "Maze":
        """Read a maze from ``path``.

        Raises:
            ValueError: when the file is not valid JSON or not a maze document.
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except OSError as exc:
            raise ValueError(f"无法读取文件：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"不是合法的 JSON 文件：{exc}") from exc
        return cls.from_json(raw)


# ---------------------------------------------------------------------------
# sample maze used by ``--sample`` and by the test suite
# ---------------------------------------------------------------------------
def build_sample_maze() -> Maze:
    """A small three-floor demo exercising every cell kind and wall state."""
    maze = Maze(width=12, height=9, layers=3)

    # --- floor 1: spiral of walls, a text cell and a portal --------------
    ground = maze.layers[0]
    for cell in ground.iter_cells():
        ground.cells[cell] = CellData(kind=GROUND)

    # A ring of solid walls with a couple of openings.
    for x in range(2, 10):
        ground.edges[edge_key((x, 1), (x + 1, 1))] = [False, False]
        ground.edges[edge_key((x, 7), (x + 1, 7))] = [False, False]
    for y in range(1, 7):
        ground.edges[edge_key((2, y), (2, y + 1))] = [False, False]
        ground.edges[edge_key((9, y), (9, y + 1))] = [False, False]
    # One-way gate on the left wall, pointing into the ring.
    ground.edges[edge_key((2, 4), (2, 5))] = [True, False]
    # One-way gate on the right wall, pointing out of the ring.
    ground.edges[edge_key((9, 3), (9, 4))] = [False, True]

    ground.cells[(3, 3)] = CellData(kind=TEXT, text="起点")
    ground.cells[(8, 6)] = CellData(kind=TEXT, text="长文本会自动缩小字号")
    ground.cells[(5, 4)] = CellData(kind=PORTAL, portal=(8, 2))
    ground.cells[(8, 2)] = CellData(kind=GROUND)

    # --- floor 2: open field with a few one-way corridors ----------------
    upper = maze.layers[1]
    for x in range(1, 11):
        upper.edges[edge_key((x, 4), (x, 5))] = [True, False]
    upper.cells[(6, 2)] = CellData(kind=TEXT, text="2F")
    upper.cells[(2, 7)] = CellData(kind=PORTAL, portal=(9, 7))
    upper.cells[(9, 7)] = CellData(kind=GROUND)

    # --- floor 3: mostly air, one platform ------------------------------
    top = maze.layers[2]
    for y in range(2, 5):
        for x in range(4, 8):
            top.cells[(x, y)] = CellData(kind=GROUND)
    top.cells[(5, 3)] = CellData(kind=TEXT, text="3F")

    maze.active = 0
    return maze
