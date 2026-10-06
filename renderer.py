"""Pillow based renderer for the maze.

Why Pillow and not plain Tk canvas items?  The specification asks for *air*
cells that are transparent, per-floor alpha (0%, 50%, 75%, 87.5%, ...) and a
strict bottom-to-top drawing order.  Tk canvas items have no alpha channel, so
the picture is composited with Pillow into RGBA images and only then handed to
Tkinter as ``PhotoImage`` objects.

Region rendering
----------------
The editor shows the maze as a grid of tiles (see :mod:`app`) and repaints only
the tiles an edit touches.  :meth:`MazeRenderer.render_region` draws one
rectangle of the picture with exactly the pixels :meth:`MazeRenderer.render`
would put there: the cells around the rectangle are visited too, so a wall
stroke that reaches into the neighbouring cell, a floor arm or a frame is not
cut off, and a portal arrow is drawn into every tile it crosses (the arrows of
the whole maze are collected once by :meth:`MazeRenderer.begin`).

Colours and opacity
-------------------
Every layer stores its three colours as ``#RRGGBBAA`` (see :mod:`maze`): the
first six digits are the colour, the last two its opacity, where ``FF`` means
fully visible and ``00`` invisible.  Each element is painted with the opacity of
its own colour, so a floor colour may be translucent while the walls on top of
it stay opaque.

Layers are composited from the bottom of the stack upwards with
``Image.alpha_composite``, so a higher floor covers a lower one according to the
opacity of its colours.  There is no automatic fading of the floors next to the
active one; :attr:`MazeRenderer.show_only_active` hides them completely instead.

面积占比 (area ratio)
--------------------
Each layer stores an ``area_ratio`` in ``0..100`` (see :mod:`maze`).  It shrinks
the floor of every cell towards the cell centre by cutting the cell into nine
blocks with four inset lines:

* the **floor** is the central block plus one *arm* towards every neighbour that
  has a floor **and** is not cut off by a solid wall (a one-way edge counts as
  connected, so the two floors still meet),
* a **wall** is drawn on the cell's own inset line (the edge of its floor),
  a **one-way gate** on the shared border while the arms meet there,
* the four **corner blocks** stay empty; their edges are only outlined by the
  outer parts of the inset lines when the perpendicular arm exists.

``area_ratio == 100`` collapses the inset lines onto the cell boundary and
reproduces the plain full-cell look exactly.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from config import Config, hex_to_rgba
from maze import AIR, PORTAL, TEXT, Cell, CellData, Maze, Layer, normalise_area_ratio

RGBA = Tuple[int, int, int, int]
#: Pixel rectangle ``(x0, y0, x1, y1)`` with ``x1`` / ``y1`` excluded.
Box = Tuple[int, int, int, int]
#: One portal arrow: its layer, its cell, its data and the box it can touch.
Arrow = Tuple[int, Cell, CellData, Tuple[float, float, float, float]]

#: Neighbour offset and the direction pointing from a cell to that neighbour for
#: each of the four sides.
_SIDES: Tuple[Tuple[str, Tuple[int, int], Tuple[float, float]], ...] = (
    ("left", (-1, 0), (-1.0, 0.0)),
    ("right", (1, 0), (1.0, 0.0)),
    ("top", (0, -1), (0.0, -1.0)),
    ("bottom", (0, 1), (0.0, 1.0)),
)

#: For every side, the side met at each end of its inset line: ``-1`` is the end
#: towards the smaller coordinate, ``+1`` the one towards the larger one.
_SIDE_ENDS: Dict[Tuple[str, int], str] = {
    ("left", -1): "top", ("left", 1): "bottom",
    ("right", -1): "top", ("right", 1): "bottom",
    ("top", -1): "left", ("top", 1): "right",
    ("bottom", -1): "left", ("bottom", 1): "right",
}


def cell_bands(area_ratio, size: int) -> Tuple[float, int]:
    """``(inset, inner)`` of a ``size`` pixel cell with that area ratio.

    ``inner`` is the side of the central floor block -- never below one pixel,
    so an area ratio of 0 still leaves a dot in the middle of the cell -- and
    ``inset`` the distance from that block to the cell boundary.
    """
    ratio = normalise_area_ratio(area_ratio)
    inner = min(size, max(1, int(round(size * ratio / 100.0))))
    return (size - inner) / 2.0, inner


#: Candidate TrueType fonts, tried in order when the configured family is not
#: resolvable through Pillow's default search path.
_FONT_CANDIDATES: Tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)


class _FontCache:
    """Small helper that finds a usable font file and caches ``ImageFont`` objects."""

    def __init__(self, family: str) -> None:
        self.family = family
        self._sizes: Dict[int, ImageFont.FreeTypeFont] = {}
        self._path: Optional[str] = self._find_font()

    def _find_font(self) -> Optional[str]:
        for candidate in _FONT_CANDIDATES:
            if os.path.exists(candidate):
                return candidate
        for root in ("C:/Windows/Fonts", "/usr/share/fonts", "/Library/Fonts"):
            if not os.path.isdir(root):
                continue
            wanted = self.family.lower().replace(" ", "")
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    stem = os.path.splitext(name)[0].lower().replace(" ", "")
                    if wanted and wanted in stem and name.lower().endswith((".ttf", ".ttc", ".otf")):
                        return os.path.join(dirpath, name)
        return None

    def get(self, size: int) -> ImageFont.ImageFont:
        size = max(4, int(size))
        cached = self._sizes.get(size)
        if cached is not None:
            return cached
        font: ImageFont.ImageFont
        if self._path:
            try:
                font = ImageFont.truetype(self._path, size)
            except OSError:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()
        self._sizes[size] = font  # type: ignore[assignment]
        return font


class MazeRenderer:
    """Turns a :class:`~maze.Maze` into a composited RGBA image."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.cell_size = config.cell_size
        self._fonts = _FontCache(str(config.text.get("font_family", "")))
        self._font_measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        self._text_cache: Dict[Tuple[str, int, int], ImageFont.ImageFont] = {}
        #: Portal arrows of the maze, cached by :meth:`begin` for region draws.
        self._arrows: Optional[List[Arrow]] = None
        #: The maze ``_arrows`` belongs to; another maze must not reuse them.
        self._arrows_maze: Optional[Maze] = None
        #: When False only walls and one-way gates are drawn (``cli.py
        #: --walls-only``); the cells still provide the geometry.
        self.show_content = True
        #: When True only the active floor is drawn ("仅显示当前层"); the other
        #: floors are left out of the composite entirely.
        self.show_only_active = False

    # -- geometry --------------------------------------------------------
    def set_cell_size(self, size: int) -> None:
        self.cell_size = max(8, int(size))

    def reload_config(self, config: Config) -> None:
        """Swap in a freshly loaded configuration and drop cached measurements."""
        self.config = config
        self.cell_size = config.cell_size
        self._fonts = _FontCache(str(config.text.get("font_family", "")))
        self._text_cache.clear()

    def image_size(self, maze: Maze) -> Tuple[int, int]:
        return maze.width * self.cell_size, maze.height * self.cell_size

    def cell_at(self, px: float, py: float) -> Optional[Cell]:
        """Pixel -> cell coordinate (``None`` when outside the grid)."""
        x = int(math.floor(px / self.cell_size))
        y = int(math.floor(py / self.cell_size))
        return (x, y)

    # -- text ------------------------------------------------------------
    def _font_for_text(self, text: str, box_w: int, box_h: int) -> ImageFont.ImageFont:
        """Largest font size (binary searched) that keeps ``text`` inside the box."""
        key = (text, box_w, box_h)
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached

        min_size = int(self.config.text.get("min_size", 5))
        ratio = float(self.config.text.get("fill_ratio", 0.88))
        usable_w = max(4, int(box_w * ratio))
        usable_h = max(4, int(box_h * ratio))

        best = self._fonts.get(min_size)
        low, high = min_size, max(min_size, int(usable_h * 2))
        while low <= high:
            mid = (low + high) // 2
            font = self._fonts.get(mid)
            try:
                left, top, right, bottom = self._font_measure.textbbox((0, 0), text, font=font)
                width, height = right - left, bottom - top
            except Exception:  # pragma: no cover - defensive
                width, height = mid * len(text), mid
            if width <= usable_w and height <= usable_h:
                best = font
                low = mid + 1
            else:
                high = mid - 1

        self._text_cache[key] = best  # type: ignore[assignment]
        return best

    # -- primitives ------------------------------------------------------
    @staticmethod
    def _dashed_line(draw: ImageDraw.ImageDraw, p0: Tuple[float, float],
                     p1: Tuple[float, float], colour: RGBA, width: int,
                     dash: List[float]) -> None:
        """Draw a dashed segment from ``p0`` to ``p1``."""
        x0, y0 = p0
        x1, y1 = p1
        total = math.hypot(x1 - x0, y1 - y0)
        if total <= 0:
            return
        ux, uy = (x1 - x0) / total, (y1 - y0) / total
        pattern = [max(1.0, float(d)) for d in dash] or [6.0, 4.0]
        pos = 0.0
        index = 0
        while pos < total:
            length = min(pattern[index % len(pattern)], total - pos)
            if index % 2 == 0:  # even segments are ink, odd ones are gaps
                sx, sy = x0 + ux * pos, y0 + uy * pos
                ex, ey = x0 + ux * (pos + length), y0 + uy * (pos + length)
                draw.line([(sx, sy), (ex, ey)], fill=colour, width=width)
            pos += length
            index += 1

    @staticmethod
    def _arrow_head(draw: ImageDraw.ImageDraw, tip: Tuple[float, float],
                    direction: Tuple[float, float], length: float,
                    half_width: float, colour: RGBA) -> None:
        """Filled triangle with its apex at ``tip`` pointing along ``direction``."""
        dx, dy = direction
        norm = math.hypot(dx, dy)
        if norm == 0:
            return
        dx, dy = dx / norm, dy / norm
        px, py = -dy, dx
        base = (tip[0] - dx * length, tip[1] - dy * length)
        draw.polygon(
            [
                tip,
                (base[0] + px * half_width, base[1] + py * half_width),
                (base[0] - px * half_width, base[1] - py * half_width),
            ],
            fill=colour,
        )

    @staticmethod
    def _arrow_line(draw: ImageDraw.ImageDraw, start: Tuple[float, float],
                    end: Tuple[float, float], colour: RGBA, width: int,
                    head_length: float) -> None:
        """Straight arrow from ``start`` to ``end`` with a filled head at ``end``."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            return
        ux, uy = dx / norm, dy / norm
        head = min(head_length, norm * 0.5)
        shaft_end = (end[0] - ux * head, end[1] - uy * head)
        draw.line([start, shaft_end], fill=colour, width=width)
        MazeRenderer._arrow_head(draw, end, (ux, uy), head, max(2.0, width * 1.1), colour)

    # -- layer drawing ---------------------------------------------------
    #: The three drawing passes of one layer, in the order they must blend.
    _PASS_FLOORS, _PASS_WALLS, _PASS_CONTENT = 0, 1, 2

    @staticmethod
    def _colour(value: Any, fallback: str) -> RGBA:
        """Colour of a layer attribute as ``(r, g, b, a)``, never raising."""
        try:
            return hex_to_rgba(value)
        except (TypeError, ValueError):
            return hex_to_rgba(fallback)

    def _layer_is_opaque(self, layer: Layer) -> bool:
        """Whether all three colours of ``layer`` are fully visible."""
        return all(self._colour(getattr(layer, name, fallback), fallback)[3] == 255
                   for name, fallback in (
                       ("object_color", "#000000FF"),
                       ("floor_color", "#FFFFFFFF"),
                       ("portal_color", "#FF00FFFF"),
                   ))

    def _draw_layer(self, image: Image.Image, maze: Maze, index: int,
                    step: Optional[int] = None, box: Optional[Box] = None,
                    origin: Tuple[int, int] = (0, 0)) -> None:
        """Draw one floor in three passes so nothing is painted over.

        1. every cell floor (the layer's ``floor_color``, white by default),
        2. every wall / one-way gate plus the frames of the empty corner blocks,
        3. text and portal arrows, which must stay readable on top of both.

        Portal arrows span several cells, so without pass 3 they would be
        covered by the floor of the neighbouring cells.  With
        :attr:`show_content` off, only pass 2 runs.  ``step`` restricts the call
        to one of the three passes; the caller then blends the passes in order,
        which is what makes translucent colours blend with what is below them.

        ``box`` limits the drawing to one rectangle of the whole picture: only
        the cells whose ink can reach it are visited, and ``origin`` says which
        pixel of the full picture the top left corner of ``image`` is.  Together
        they let the editor repaint a single tile instead of the whole maze.
        """
        layer = maze.layer(index)
        # Colours (and their opacity) are stored per layer (see maze.Layer).
        object_colour = self._colour(getattr(layer, "object_color", None), "#000000FF")
        floor_colour = self._colour(getattr(layer, "floor_color", None), "#FFFFFFFF")
        size = self.cell_size
        inset, inner = cell_bands(getattr(layer, "area_ratio", 100), size)
        draw = ImageDraw.Draw(image)

        # 1) floors: the central block of each cell plus one arm per neighbour
        #    that has a floor, so two neighbouring floors always meet.
        if self.show_content and step in (None, self._PASS_FLOORS):
            self._draw_floors(draw, layer, floor_colour, inset, inner, box, origin)

        # 2) walls / one-way gates on the inset lines, and the frames that
        #    outline the empty corner blocks.
        if step in (None, self._PASS_WALLS):
            self._draw_walls(draw, layer, object_colour, inset, inner, box, origin)

        # 3) text and portal arrows
        if not self.show_content or step not in (None, self._PASS_CONTENT):
            return
        ox, oy = origin
        for (cx, cy), data in self._iter_cells(layer, box):
            if data.kind != TEXT:
                continue
            x0, y0 = cx * size - ox, cy * size - oy
            font = self._font_for_text(data.text, size, size)
            draw.text((x0 + size / 2, y0 + size / 2), data.text,
                      fill=object_colour, font=font, anchor="mm")
        # Arrows are picked from the whole maze (an arrow of a far away portal
        # may still cross this rectangle), so they are not taken from the cell
        # loop above -- that also keeps a translucent arrow from being painted
        # twice.
        for _layer_index, cell, data, _arrow_box in self._arrows_for(index, box):
            self._draw_portal(draw, maze, layer, cell, data, origin)

    @staticmethod
    def _has_floor(layer: Layer, x: int, y: int) -> bool:
        """Whether the neighbour at ``(x, y)`` carries a floor of its own."""
        return layer.contains(x, y) and layer.get_cell(x, y).kind != AIR

    @classmethod
    def _arm_exists(cls, layer: Layer, cell: Cell, offset: Tuple[int, int]) -> bool:
        """Whether ``cell``'s floor reaches the neighbour in that direction.

        The edge block ("arm") is painted when the neighbour has a floor *and*
        the shared edge is not a solid wall.  A one-way edge counts as
        connected, so the floors of its two cells still meet.
        """
        neighbour = (cell[0] + offset[0], cell[1] + offset[1])
        if not cls._has_floor(layer, neighbour[0], neighbour[1]):
            return False
        forward, backward = layer.edge_state(cell, neighbour)
        return bool(forward or backward)

    def _draw_floors(self, draw: ImageDraw.ImageDraw, layer: Layer, colour: RGBA,
                     inset: float, inner: int, box: Optional[Box] = None,
                     origin: Tuple[int, int] = (0, 0)) -> None:
        """Paint the central block of every cell plus its arms.

        An arm reaches towards a neighbour that has a floor when the shared edge
        is not a solid wall, so the floors of two cells meet across an open or
        one-way edge and stop at their own inset lines at a wall.
        """
        size = self.cell_size
        ox, oy = origin
        for (cx, cy), data in self._iter_cells(layer, box):
            if data.kind == AIR:
                continue
            x0, y0 = cx * size - ox, cy * size - oy
            left, top = x0 + inset, y0 + inset
            right, bottom = left + inner, top + inner
            draw.rectangle([left, top, right, bottom], fill=colour)
            if inset <= 0:
                continue  # area ratio 100: the central block is the whole cell
            for name, (dx, dy), _vec in _SIDES:
                if not self._arm_exists(layer, (cx, cy), (dx, dy)):
                    continue
                if name == "left":
                    arm = [x0, top, left, bottom]
                elif name == "right":
                    arm = [right, top, x0 + size, bottom]
                elif name == "top":
                    arm = [left, y0, right, top]
                else:
                    arm = [left, bottom, right, y0 + size]
                draw.rectangle(arm, fill=colour)

    def _side_points(self, side: str, x0: float, y0: float, inset: float,
                     inner: int, part: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Endpoints of one part of a cell's inset line for ``side``.

        ``part`` is ``-1`` for the outer part at the smaller coordinate, ``0``
        for the middle part and ``+1`` for the outer part at the larger one.
        """
        size = self.cell_size
        if part < 0:
            low, high = 0.0, inset
        elif part > 0:
            low, high = inset + inner, float(size)
        else:
            low, high = inset, inset + inner
        if side == "left":
            x = x0 + inset
            return (x, y0 + low), (x, y0 + high)
        if side == "right":
            x = x0 + size - inset
            return (x, y0 + low), (x, y0 + high)
        if side == "top":
            y = y0 + inset
            return (x0 + low, y), (x0 + high, y)
        y = y0 + size - inset
        return (x0 + low, y), (x0 + high, y)

    def _border_points(self, side: str, x0: float, y0: float, inset: float,
                       inner: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Endpoints of the middle part of a cell's own boundary line.

        A one-way gate sits here when the floor reaches the neighbour: the arms
        of the two cells meet on the shared border, so that is where the gate
        belongs (at area ratio 100 this is the inset line again).
        """
        size = self.cell_size
        low, high = inset, inset + inner
        if side == "left":
            return (x0, y0 + low), (x0, y0 + high)
        if side == "right":
            return (x0 + size, y0 + low), (x0 + size, y0 + high)
        if side == "top":
            return (x0 + low, y0), (x0 + high, y0)
        return (x0 + low, y0 + size), (x0 + high, y0 + size)

    def _draw_walls(self, draw: ImageDraw.ImageDraw, layer: Layer, colour: RGBA,
                    inset: float, inner: int, box: Optional[Box] = None,
                    origin: Tuple[int, int] = (0, 0)) -> None:
        """Walls, one-way gates and the frames around the empty corner blocks.

        Only cells that have a floor draw anything: an air cell paints no wall,
        so an edge between two air cells stays invisible.
        """
        size = self.cell_size
        ox, oy = origin
        # Wall width is a property of the floor being drawn.
        wall_width = max(1, int(getattr(layer, "wall_width", 3)))
        arrow_width = max(1, int(self.config.wall.get("arrow_width", wall_width)))
        dash = list(self.config.wall.get("dash", [6, 4]))
        # The head has to fit into the middle part of the line it sits on.
        arrow_len = float(self.config.wall.get("arrow_length", 0.22)) * size
        head_len = max(2.0, min(arrow_len, inner * 0.5))

        for (cx, cy), data in self._iter_cells(layer, box):
            if data.kind == AIR:
                continue
            x0, y0 = cx * size - ox, cy * size - oy
            arms = {name: self._arm_exists(layer, (cx, cy), (dx, dy))
                    for name, (dx, dy), _vec in _SIDES}

            for name, (dx, dy), direction in _SIDES:
                # The two outer parts outline the empty corner blocks; they are
                # drawn when the arm of the perpendicular side exists, because
                # only then has the part a floor on one side.
                for part in (-1, 1):
                    if not arms[_SIDE_ENDS[(name, part)]]:
                        continue
                    p0, p1 = self._side_points(name, x0, y0, inset, inner, part)
                    if p0 != p1:  # a zero-length part (area ratio 100) is skipped
                        draw.line([p0, p1], fill=colour, width=wall_width)

                # The middle part carries the wall or the one-way gate.
                nx, ny = cx + dx, cy + dy
                if not layer.contains(nx, ny):
                    continue
                forward, backward = layer.edge_state((cx, cy), (nx, ny))
                if forward and backward:
                    continue           # open passage: nothing to draw
                if backward and not forward:
                    continue           # the traveller's cell draws the gate
                if forward:
                    # One-way gate: dashed line + arrow head showing the
                    # allowed travel direction.  It sits on the shared border
                    # while the floors meet there, otherwise on the floor edge.
                    p0, p1 = (self._border_points(name, x0, y0, inset, inner)
                              if arms[name] else
                              self._side_points(name, x0, y0, inset, inner, 0))
                    self._dashed_line(draw, p0, p1, colour, wall_width, dash)
                    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
                    self._arrow_head(draw, mid, direction, head_len,
                                     max(2.0, arrow_width * 1.1), colour)
                else:
                    # A solid wall follows the edge of the floor, which is this
                    # cell's inset line -- a walled edge never has an arm.
                    p0, p1 = self._side_points(name, x0, y0, inset, inner, 0)
                    draw.line([p0, p1], fill=colour, width=wall_width)

    def _draw_portal(self, draw: ImageDraw.ImageDraw, maze: Maze, layer,
                     cell: Cell, data: CellData,
                     origin: Tuple[int, int] = (0, 0)) -> None:
        """Arrow from the cell centre towards the destination cell centre."""
        size = self.cell_size
        ox, oy = origin
        start = ((cell[0] + 0.5) * size - ox, (cell[1] + 0.5) * size - oy)
        target = data.portal
        # Portal colour and wall width belong to the floor drawing the arrow.
        colour = hex_to_rgba(getattr(layer, "portal_color", "#FF00FFFF"))
        wall_width = max(1, int(getattr(layer, "wall_width", 3)))
        if target is None or not layer.contains(*target):
            # Destination not set / out of bounds: mark the cell with a ring.
            radius = size * 0.28
            draw.ellipse(
                [start[0] - radius, start[1] - radius, start[0] + radius, start[1] + radius],
                outline=colour, width=max(2, wall_width),
            )
            return

        end = ((target[0] + 0.5) * size - ox, (target[1] + 0.5) * size - oy)
        head = max(4.0, float(self.config.wall.get("arrow_length", 0.22)) * size)
        self._arrow_line(draw, start, end, colour, wall_width, head)

    # -- region helpers --------------------------------------------------
    def begin(self, maze: Maze) -> None:
        """Prepare a batch of :meth:`render_region` calls.

        Caches the portal arrows of the whole maze, because an arrow may cross a
        region although its own cell lies outside it.  Call this once before
        repainting a set of tiles; :meth:`render` and the editor's refresh paths
        do it for you.  The cache is *always* rebuilt, so arrows can never
        outlive the maze (or the edit) they came from.
        """
        arrows = []
        for index in range(maze.layer_count):
            layer = maze.layer(index)
            for cell, data in layer.cells.items():
                if data.kind == PORTAL:
                    arrows.append((index, cell, data, self.arrow_box(layer, cell, data)))
        self._arrows = arrows
        self._arrows_maze = maze

    def _ensure_arrows(self, maze: Maze) -> None:
        """Use the arrows of ``maze``, collecting them when they are not cached.

        The cached list belongs to the maze that was passed to :meth:`begin`, so
        a region draw of a *different* maze must not reuse it -- that used to
        paint the portal arrows of a previously loaded document into the new
        one.
        """
        if self._arrows is None or self._arrows_maze is not maze:
            self.begin(maze)

    def arrow_box(self, layer: Layer, cell: Cell,
                  data: CellData) -> Tuple[float, float, float, float]:
        """Pixel rectangle a portal arrow can touch (``(x0, y0, x1, y1)``)."""
        size = self.cell_size
        centre = ((cell[0] + 0.5) * size, (cell[1] + 0.5) * size)
        wall_width = max(1, int(getattr(layer, "wall_width", 3)))
        target = data.portal
        if target is None or not layer.contains(*target):
            radius = size * 0.28 + max(2, wall_width) + 1
            return (centre[0] - radius, centre[1] - radius,
                    centre[0] + radius, centre[1] + radius)
        other = ((target[0] + 0.5) * size, (target[1] + 0.5) * size)
        pad = max(size * 0.25, wall_width * 1.5) + 2
        return (min(centre[0], other[0]) - pad, min(centre[1], other[1]) - pad,
                max(centre[0], other[0]) + pad, max(centre[1], other[1]) + pad)

    def _arrows_for(self, index: int, box: Optional[Box]):
        """Yield ``(layer index, cell, data, box)`` of the arrows to draw.

        With ``box`` the arrows whose own cell is in the box *and* those that
        merely cross it are returned; without a box every arrow of the layer is.
        """
        arrows = self._arrows
        if arrows is None:
            return
        for entry in arrows:
            if entry[0] != index:
                continue
            if box is None or self._boxes_intersect(entry[3], box):
                yield entry

    @staticmethod
    def _boxes_intersect(a: Tuple[float, ...], b: Box) -> bool:
        return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])

    def _cell_range(self, layer: Layer, box: Box) -> Tuple[int, int, int, int]:
        """Half open cell range whose ink can reach the pixel ``box``.

        A wall stroke is centred on the inset line, which at an area ratio of
        100 lies on the cell boundary, so ink reaches up to half a wall width
        into the neighbouring cell.
        """
        size = self.cell_size
        pad = max(1, int(getattr(layer, "wall_width", 3))) // 2 + 2
        x0 = max(0, int(math.floor((box[0] - pad) / size)))
        y0 = max(0, int(math.floor((box[1] - pad) / size)))
        x1 = min(layer.width, int(math.floor((box[2] - 1 + pad) / size)) + 1)
        y1 = min(layer.height, int(math.floor((box[3] - 1 + pad) / size)) + 1)
        return x0, y0, x1, y1

    def _iter_cells(self, layer: Layer, box: Optional[Box]):
        """Yield ``(cell, data)`` of the non-air cells that ``box`` needs.

        Without a box the layer's own sparse dict is walked; with one only the
        cells of the covered range are looked up, which is what keeps a tile
        redraw proportional to the tile instead of to the maze.
        """
        if box is None:
            for cell, data in layer.cells.items():
                if data.kind != AIR:
                    yield cell, data
            return
        x0, y0, x1, y1 = self._cell_range(layer, box)
        cells = layer.cells
        for cy in range(y0, y1):
            for cx in range(x0, x1):
                data = cells.get((cx, cy))
                if data is not None and data.kind != AIR:
                    yield (cx, cy), data

    def _clip_box(self, maze: Maze, box: Box) -> Box:
        """Clamp a pixel box to the picture; may come back empty."""
        width, height = self.image_size(maze)
        x0 = max(0, min(int(box[0]), width))
        y0 = max(0, min(int(box[1]), height))
        x1 = max(x0, min(int(box[2]), width))
        y1 = max(y0, min(int(box[3]), height))
        return x0, y0, x1, y1

    # -- compositing -----------------------------------------------------
    def render_layer(self, maze: Maze, index: int) -> Image.Image:
        """Draw one floor alone on a transparent canvas.

        Within the floor the three passes are blended in order, so a translucent
        wall blends with the floor it stands on instead of replacing it.  A layer
        whose colours are all opaque is drawn in a single pass, as before.
        """
        self.begin(maze)
        return self._layer_image(maze, index)

    def _layer_image(self, maze: Maze, index: int) -> Image.Image:
        """Composite one floor, using the arrows cached by :meth:`begin`."""
        size = self.image_size(maze)
        layer = maze.layer(index)
        if self._layer_is_opaque(layer):
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            self._draw_layer(canvas, maze, index)
            return canvas

        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for step in (self._PASS_FLOORS, self._PASS_WALLS, self._PASS_CONTENT):
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            self._draw_layer(overlay, maze, index, step)
            canvas = Image.alpha_composite(canvas, overlay)
        return canvas

    def render_layer_region(self, maze: Maze, index: int, box: Box) -> Image.Image:
        """Like :meth:`render_layer` but only the pixels inside ``box``."""
        box = self._clip_box(maze, box)
        size = (box[2] - box[0], box[3] - box[1])
        if size[0] <= 0 or size[1] <= 0:
            return Image.new("RGBA", (max(0, size[0]), max(0, size[1])), (0, 0, 0, 0))
        layer = maze.layer(index)
        origin = (box[0], box[1])
        self._ensure_arrows(maze)
        if self._layer_is_opaque(layer):
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            self._draw_layer(canvas, maze, index, None, box, origin)
            return canvas

        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for step in (self._PASS_FLOORS, self._PASS_WALLS, self._PASS_CONTENT):
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            self._draw_layer(overlay, maze, index, step, box, origin)
            canvas = Image.alpha_composite(canvas, overlay)
        return canvas

    def _layer_indices(self, maze: Maze, active_index: Optional[int]) -> List[int]:
        if self.show_only_active:
            if active_index is None:
                active_index = maze.active
            return [max(0, min(int(active_index), maze.layer_count - 1))]
        return list(range(maze.layer_count))

    def render_region(self, maze: Maze, box: Box,
                      active_index: Optional[int] = None) -> Image.Image:
        """Composite one rectangle of the picture (same pixels as :meth:`render`).

        The editor uses this to repaint a single tile after an edit instead of
        the whole maze.  Call :meth:`begin` once before a batch of region draws.
        """
        box = self._clip_box(maze, box)
        size = (box[2] - box[0], box[3] - box[1])
        if size[0] <= 0 or size[1] <= 0:
            return Image.new("RGBA", (max(0, size[0]), max(0, size[1])), (0, 0, 0, 0))
        self._ensure_arrows(maze)
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for index in self._layer_indices(maze, active_index):
            canvas = Image.alpha_composite(canvas, self.render_layer_region(maze, index, box))
        return canvas

    def render(self, maze: Maze, active_index: Optional[int] = None) -> Image.Image:
        """Composite the layers bottom-to-top into one RGBA image.

        Every layer paints itself with the opacity of its own colours, so a
        higher floor covers the lower ones.  With :attr:`show_only_active` the
        composite is limited to the active floor ("仅显示当前层").
        """
        self.begin(maze)
        width, height = self.image_size(maze)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        for index in self._layer_indices(maze, active_index):
            canvas = Image.alpha_composite(canvas, self._layer_image(maze, index))
        return canvas
