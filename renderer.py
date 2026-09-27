"""Pillow based renderer for the maze.

Why Pillow and not plain Tk canvas items?  The specification asks for *air*
cells that are transparent, per-floor alpha (0%, 50%, 75%, 87.5%, ...) and a
strict bottom-to-top drawing order.  Tk canvas items have no alpha channel, so
the whole picture is composited into one RGBA image which is then handed to
Tkinter as a single ``PhotoImage``.

Layer opacity
-------------
``alpha(distance) = 0.5 ** distance`` where ``distance`` is
``abs(layer_index - active_index)``:

======  =========  ============================
distance  alpha     transparency
======  =========  ============================
0        1.0       0 %      (current floor)
1        0.5       50 %     (one above/below)
2        0.25      75 %     (two above/below)
3        0.125     87.5 %
======  =========  ============================

Layers are composited from the bottom of the stack upwards, so a higher floor
is painted over a lower one.

面积占比 (area ratio)
--------------------
Each layer stores an ``area_ratio`` in ``0..100`` (see :mod:`maze`).  It shrinks
the floor of every cell towards the cell centre and pulls the walls inwards:
the floor becomes the central block plus one arm towards each neighbour that has
a floor, the walls sit on the four inset lines, and the empty corner blocks are
outlined by the outer parts of those lines.  ``area_ratio == 100`` reproduces
the plain full-cell look exactly.
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from config import Config, hex_to_rgb
from maze import AIR, PORTAL, TEXT, Cell, CellData, Maze, Layer, normalise_area_ratio

RGB = Tuple[int, int, int]

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

#: Default colour of a floor tile, used when ``floor_color_group`` is missing.
WHITE: RGB = (255, 255, 255)

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


def alpha_for_distance(distance: int) -> int:
    """0-255 opacity for a layer ``distance`` steps away from the active one."""
    distance = abs(int(distance))
    return max(1, int(round(255.0 * (0.5 ** distance))))


def transparency_percent_for_distance(distance: int) -> float:
    """Transparency in percent, for display in the UI status bar."""
    return 100.0 * (1.0 - 0.5 ** abs(int(distance)))


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
        #: When False only walls and one-way gates are drawn (``cli.py
        #: --walls-only``); the cells still provide the geometry.
        self.show_content = True

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
                     p1: Tuple[float, float], colour: RGB, width: int,
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
                    half_width: float, colour: RGB) -> None:
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
                    end: Tuple[float, float], colour: RGB, width: int,
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
    def _draw_layer(self, image: Image.Image, maze: Maze, index: int) -> None:
        """Draw one floor in three passes so nothing is painted over.

        1. every cell floor (the layer's ``floor_color``, white by default),
        2. every wall / one-way gate plus the frames of the empty corner blocks,
        3. text and portal arrows, which must stay readable on top of both.

        Portal arrows span several cells, so without pass 3 they would be
        covered by the floor of the neighbouring cells.  With
        :attr:`show_content` off, only pass 2 runs.
        """
        layer = maze.layer(index)
        # Colours are stored per layer (see maze.Layer).
        object_colour = hex_to_rgb(getattr(layer, "object_color", "#000000"))
        floor_colour = hex_to_rgb(getattr(layer, "floor_color", "#FFFFFF"))
        size = self.cell_size
        inset, inner = cell_bands(getattr(layer, "area_ratio", 100), size)
        draw = ImageDraw.Draw(image)

        # 1) floors: the central block of each cell plus one arm per neighbour
        #    that has a floor, so two neighbouring floors always meet.
        if self.show_content:
            self._draw_floors(draw, layer, floor_colour, inset, inner)

        # 2) walls / one-way gates on the inset lines, and the frames that
        #    outline the empty corner blocks.
        self._draw_walls(draw, layer, object_colour, inset, inner)

        # 3) text and portal arrows
        if not self.show_content:
            return
        for (cx, cy), data in layer.cells.items():
            if data.kind == TEXT:
                x0, y0 = cx * size, cy * size
                font = self._font_for_text(data.text, size, size)
                draw.text((x0 + size / 2, y0 + size / 2), data.text,
                          fill=object_colour, font=font, anchor="mm")
            elif data.kind == PORTAL:
                self._draw_portal(draw, maze, layer, (cx, cy), data)

    @staticmethod
    def _has_floor(layer: Layer, x: int, y: int) -> bool:
        """Whether the neighbour at ``(x, y)`` carries a floor of its own."""
        return layer.contains(x, y) and layer.get_cell(x, y).kind != AIR

    def _draw_floors(self, draw: ImageDraw.ImageDraw, layer: Layer, colour: RGB,
                     inset: float, inner: int) -> None:
        """Paint the central block of every cell plus its arms.

        An arm reaches towards a neighbour that has a floor, whether or not the
        shared edge is passable: the floor of two adjacent cells always meets,
        and a walled edge is drawn on top of it.
        """
        size = self.cell_size
        for (cx, cy), data in layer.cells.items():
            if data.kind == AIR:
                continue
            x0, y0 = cx * size, cy * size
            left, top = x0 + inset, y0 + inset
            right, bottom = left + inner, top + inner
            draw.rectangle([left, top, right, bottom], fill=colour)
            if inset <= 0:
                continue  # area ratio 100: the central block is the whole cell
            for name, (dx, dy), _vec in _SIDES:
                if not self._has_floor(layer, cx + dx, cy + dy):
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

    def _draw_walls(self, draw: ImageDraw.ImageDraw, layer: Layer, colour: RGB,
                    inset: float, inner: int) -> None:
        """Walls, one-way gates and the frames around the empty corner blocks.

        Only cells that have a floor draw anything: an air cell paints no wall,
        so an edge between two air cells stays invisible.
        """
        size = self.cell_size
        # Wall width is a property of the floor being drawn.
        wall_width = max(1, int(getattr(layer, "wall_width", 3)))
        arrow_width = max(1, int(self.config.wall.get("arrow_width", wall_width)))
        dash = list(self.config.wall.get("dash", [6, 4]))
        # The head has to fit into the middle part of the inset line.
        arrow_len = float(self.config.wall.get("arrow_length", 0.22)) * size
        head_len = max(2.0, min(arrow_len, inner * 0.5))

        for (cx, cy), data in layer.cells.items():
            if data.kind == AIR:
                continue
            x0, y0 = cx * size, cy * size
            floor_side = {name: self._has_floor(layer, cx + dx, cy + dy)
                          for name, (dx, dy), _vec in _SIDES}

            for name, (dx, dy), direction in _SIDES:
                # The two outer parts outline the empty corner blocks; they are
                # drawn when the neighbour perpendicular to this side has a
                # floor, because only then has the part a floor on one side.
                for part in (-1, 1):
                    if not floor_side[_SIDE_ENDS[(name, part)]]:
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
                p0, p1 = self._side_points(name, x0, y0, inset, inner, 0)
                if forward:
                    # One-way gate: dashed line + arrow head showing the
                    # allowed travel direction.
                    self._dashed_line(draw, p0, p1, colour, wall_width, dash)
                    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
                    self._arrow_head(draw, mid, direction, head_len,
                                     max(2.0, arrow_width * 1.1), colour)
                else:
                    draw.line([p0, p1], fill=colour, width=wall_width)

    def _draw_portal(self, draw: ImageDraw.ImageDraw, maze: Maze, layer,
                     cell: Cell, data: CellData) -> None:
        """Arrow from the cell centre towards the destination cell centre."""
        size = self.cell_size
        start = ((cell[0] + 0.5) * size, (cell[1] + 0.5) * size)
        target = data.portal
        # Portal colour and wall width belong to the floor drawing the arrow.
        colour = hex_to_rgb(getattr(layer, "portal_color", "#FF00FF"))
        wall_width = max(1, int(getattr(layer, "wall_width", 3)))
        if target is None or not layer.contains(*target):
            # Destination not set / out of bounds: mark the cell with a ring.
            radius = size * 0.28
            draw.ellipse(
                [start[0] - radius, start[1] - radius, start[0] + radius, start[1] + radius],
                outline=colour, width=max(2, wall_width),
            )
            return

        end = ((target[0] + 0.5) * size, (target[1] + 0.5) * size)
        head = max(4.0, float(self.config.wall.get("arrow_length", 0.22)) * size)
        self._arrow_line(draw, start, end, colour, wall_width, head)

    # -- compositing -----------------------------------------------------
    def render_layer(self, maze: Maze, index: int) -> Image.Image:
        """Draw one floor alone on a transparent canvas (no alpha scaling)."""
        canvas = Image.new("RGBA", self.image_size(maze), (0, 0, 0, 0))
        self._draw_layer(canvas, maze, index)
        return canvas

    def render(self, maze: Maze, active_index: Optional[int] = None) -> Image.Image:
        """Composite every layer bottom-to-top into one RGBA image."""
        if active_index is None:
            active_index = maze.active
        width, height = self.image_size(maze)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        for index in range(maze.layer_count):
            alpha = alpha_for_distance(index - active_index)
            if alpha <= 0:
                continue
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            self._draw_layer(overlay, maze, index)
            if alpha < 255:
                # Scale the overlay's alpha channel, then blend it over the canvas.
                r, g, b, a = overlay.split()
                a = a.point(lambda value, factor=alpha: (value * factor) // 255)
                overlay = Image.merge("RGBA", (r, g, b, a))
            canvas = Image.alpha_composite(canvas, overlay)
        return canvas

    def render_on_checkerboard(self, maze: Maze, active_index: Optional[int] = None,
                               square: int = 12) -> Image.Image:
        """Like :meth:`render` but over a checkerboard, for headless export."""
        image = self.render(maze, active_index)
        width, height = image.size
        board = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(board)
        for y in range(0, height, square):
            for x in range(0, width, square):
                if ((x // square) + (y // square)) % 2:
                    draw.rectangle([x, y, x + square - 1, y + square - 1], fill=(222, 222, 222, 255))
        return Image.alpha_composite(board, image)
