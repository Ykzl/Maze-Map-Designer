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
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from config import Config, hex_to_rgb
from maze import AXIS_X, AIR, GROUND, PORTAL, TEXT, Cell, CellData, Maze, edge_cells

RGB = Tuple[int, int, int]

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

        1. every cell floor (white rectangles),
        2. every wall / one-way gate,
        3. text and portal arrows, which must stay readable on top of both.

        Portal arrows span several cells, so without pass 3 they would be
        covered by the white floor of the neighbouring cells.
        """
        layer = maze.layer(index)
        colour = hex_to_rgb(self.config.color_for_floor(maze.floor_of(index)))
        size = self.cell_size
        draw = ImageDraw.Draw(image)

        # 1) floors
        for (cx, cy), data in layer.cells.items():
            if data.kind == AIR:
                continue
            x0, y0 = cx * size, cy * size
            draw.rectangle([x0, y0, x0 + size, y0 + size], fill=WHITE)

        # 2) thin walls / one-way gates
        self._draw_edges(draw, layer, colour)

        # 3) text and portal arrows
        for (cx, cy), data in layer.cells.items():
            if data.kind == TEXT:
                x0, y0 = cx * size, cy * size
                font = self._font_for_text(data.text, size, size)
                draw.text((x0 + size / 2, y0 + size / 2), data.text,
                          fill=colour, font=font, anchor="mm")
            elif data.kind == PORTAL:
                self._draw_portal(draw, maze, layer, (cx, cy), data)

    def _draw_edges(self, draw: ImageDraw.ImageDraw, layer, colour: RGB) -> None:
        size = self.cell_size
        wall_width = max(1, int(self.config.wall.get("width", 3)))
        arrow_width = max(1, int(self.config.wall.get("arrow_width", wall_width)))
        dash = list(self.config.wall.get("dash", [6, 4]))
        arrow_len = float(self.config.wall.get("arrow_length", 0.22)) * size

        for key, state in layer.edges.items():
            forward, backward = bool(state[0]), bool(state[1])
            cx, cy, axis = key
            low, high = edge_cells(key)

            if axis == AXIS_X:
                px = (cx + 1) * size
                y0, y1 = cy * size, (cy + 1) * size
                p0, p1 = (px, y0), (px, y1)
                mid = (px, (y0 + y1) / 2)
                axis_vec = (1.0, 0.0)
            else:
                py = (cy + 1) * size
                x0, x1 = cx * size, (cx + 1) * size
                p0, p1 = (x0, py), (x1, py)
                mid = ((x0 + x1) / 2, py)
                axis_vec = (0.0, 1.0)

            if not forward and not backward:
                draw.line([p0, p1], fill=colour, width=wall_width)
            else:
                # One-way gate: dashed boundary + arrow head showing the
                # allowed travel direction (drawn only inside the maze).
                self._dashed_line(draw, p0, p1, colour, wall_width, dash)
                direction = axis_vec if forward else (-axis_vec[0], -axis_vec[1])
                self._arrow_head(draw, mid, direction, arrow_len,
                                 max(2.0, arrow_width * 1.1), colour)

    def _draw_portal(self, draw: ImageDraw.ImageDraw, maze: Maze, layer,
                     cell: Cell, data: CellData) -> None:
        """Arrow from the cell centre towards the destination cell centre."""
        size = self.cell_size
        start = ((cell[0] + 0.5) * size, (cell[1] + 0.5) * size)
        target = data.portal
        colour = hex_to_rgb(self.config.portal_color)
        if target is None or not layer.contains(*target):
            # Destination not set / out of bounds: mark the cell with a ring.
            radius = size * 0.28
            draw.ellipse(
                [start[0] - radius, start[1] - radius, start[0] + radius, start[1] + radius],
                outline=colour, width=max(2, int(self.config.wall.get("width", 3))),
            )
            return

        end = ((target[0] + 0.5) * size, (target[1] + 0.5) * size)
        width = max(1, int(self.config.wall.get("width", 3)))
        head = max(4.0, float(self.config.wall.get("arrow_length", 0.22)) * size)
        self._arrow_line(draw, start, end, colour, width, head)

    # -- compositing -----------------------------------------------------
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
