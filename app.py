"""Tkinter GUI for the multi-floor maze map designer.

Run with::

    python app.py                  # start with a small demo maze
    python app.py path/to/maze.json

Interaction summary
-------------------
* **左键**  — apply the current brush (cell kind / wall state / portal step)
* **右键**  — the inverse of the current brush (erase cell / cycle wall state)
* **中键拖动 / 方向键** — pan the view
* **Ctrl+滚轮 或 Z / X** — zoom the cell size
* **PageUp / PageDown** — previous / next floor, **Home / End** — lowest / top floor
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageTk

from config import Config, default_config_path, hex_to_rgba
from maze import (
    AIR,
    CELL_KIND_LABELS,
    DEFAULT_AREA_RATIO,
    DEFAULT_FLOOR_COLOR,
    DEFAULT_OBJECT_COLOR,
    DEFAULT_PORTAL_COLOR,
    DEFAULT_WALL_WIDTH,
    GROUND,
    PORTAL,
    TEXT,
    Cell,
    CellData,
    Layer,
    Maze,
    build_sample_maze,
    edge_cells,
    edge_key,
    normalise_area_ratio,
    normalise_color,
    normalise_wall_width,
)

APP_TITLE = "迷宫地图编辑器 · Maze Map Designer"

#: Colour of the canvas behind the maze.  It is also the colour the render is
#: composited over before it is handed to Tk -- see ``MazeEditorApp._flatten``.
CANVAS_BACKGROUND = "#f0f0f0"
_CANVAS_BACKGROUND_RGBA = hex_to_rgba(CANVAS_BACKGROUND)

#: Cells per tile.  The picture is built as a grid of Tk photos ("tiles") so an
#: edit only has to redraw the tiles it touches instead of the whole maze.
TILE_CELLS = 8

#: Brush identifiers used by the toolbar combobox.
BRUSH_CELL = "单元内容"
BRUSH_WALL = "墙"
BRUSH_LABELS = (BRUSH_CELL, BRUSH_WALL)

#: Cell kinds that need a dialog or a second click, so a drag stroke must not
#: paint them over and over while the mouse moves.
NON_DRAGGABLE_KINDS = (PORTAL, TEXT)

#: Wall brush states, in cycling order for the right mouse button.
WALL_STATES = ("open", "wall", "oneway")
WALL_STATE_LABELS = {
    "open": "连通",
    "wall": "墙",
    "oneway": "单向板",
}
WALL_STATE_CYCLE = {"open": "wall", "wall": "oneway", "oneway": "open"}


class MazeEditorApp:
    """The editor window."""

    def __init__(self, root: tk.Tk, maze: Maze, config: Config,
                 path: Optional[str] = None) -> None:
        self.root = root
        self.maze = maze
        self.config = config
        self.path = path
        self.dirty = False

        from renderer import MazeRenderer  # local import keeps headless use cheap

        self.renderer = MazeRenderer(config)
        self._checker_photo: Optional[ImageTk.PhotoImage] = None
        #: One Tk photo and one canvas item per tile, keyed by ``(col, row)``.
        self._tile_photos: Dict[Tuple[int, int], ImageTk.PhotoImage] = {}
        self._tile_items: Dict[Tuple[int, int], int] = {}
        #: Cells edited since the last redraw; they decide which tiles repaint.
        self._dirty_cells: set = set()
        #: Tiles that must repaint although no single cell covers them (arrows).
        self._dirty_tiles: set = set()
        #: Opaque backgrounds the render is flattened over, keyed by picture size.
        self._boards: Dict[Tuple[int, int], Image.Image] = {}

        self.pending_portal_source: Optional[Cell] = None

        #: Cells already touched by the current left-button drag, so a stroke
        #: paints each cell once even when the mouse wiggles inside it.
        self._drag_cells: set = set()
        self._dragging: bool = False
        #: Last cell touched by the wall brush during the current stroke.
        self._last_edge: Optional[Cell] = None

        # -- tk variables -------------------------------------------------
        self.brush_var = tk.StringVar(value=BRUSH_CELL)
        self.cell_kind_var = tk.StringVar(value=CELL_KIND_LABELS[GROUND])
        self.wall_state_var = tk.StringVar(value=WALL_STATE_LABELS["wall"])
        self.width_var = tk.IntVar(value=maze.width)
        self.height_var = tk.IntVar(value=maze.height)
        self.layer_count_var = tk.IntVar(value=maze.layer_count)
        self.cell_size_var = tk.IntVar(value=self.renderer.cell_size)
        #: Appearance of the current floor, edited in the third toolbar row.
        #: Strings (not IntVar) because a half-typed value must stay visible.
        self.object_color_var = tk.StringVar(value=DEFAULT_OBJECT_COLOR)
        self.floor_color_var = tk.StringVar(value=DEFAULT_FLOOR_COLOR)
        self.portal_color_var = tk.StringVar(value=DEFAULT_PORTAL_COLOR)
        self.wall_width_var = tk.StringVar(value=str(DEFAULT_WALL_WIDTH))
        self.area_ratio_var = tk.StringVar(value=str(DEFAULT_AREA_RATIO))
        #: View option: draw the active floor alone, without the other floors.
        self.only_active_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self.coord_var = tk.StringVar(value="")
        self.layer_info_var = tk.StringVar(value="")

        self._build_ui()
        self._bind_events()
        self.sync_style_fields()
        self.refresh(fit=True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("1200x800")
        self.root.minsize(760, 520)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        # --- file -------------------------------------------------------
        ttk.Button(toolbar, text="新建", command=self.new_maze, width=6).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="打开", command=self.open_maze, width=6).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text="保存", command=self.save_maze, width=6).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text="另存为", command=self.save_maze_as, width=7).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # --- maze size --------------------------------------------------
        ttk.Label(toolbar, text="宽").pack(side=tk.LEFT)
        ttk.Spinbox(toolbar, from_=1, to=512, width=5, textvariable=self.width_var).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(toolbar, text="高").pack(side=tk.LEFT)
        ttk.Spinbox(toolbar, from_=1, to=512, width=5, textvariable=self.height_var).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Button(toolbar, text="应用尺寸", command=self.apply_size, width=9).pack(side=tk.LEFT)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # --- layers -----------------------------------------------------
        ttk.Label(toolbar, text="层数").pack(side=tk.LEFT)
        #: Read-only display of the stack height (a Label, not an input).
        self.layer_count_label = ttk.Label(toolbar, textvariable=self.layer_count_var,
                                           width=4, anchor=tk.CENTER, relief=tk.SUNKEN,
                                           padding=(4, 1))
        self.layer_count_label.pack(side=tk.LEFT, padx=(2, 6))
        ttk.Button(toolbar, text="在当前楼层上插入楼层", command=lambda: self.insert_layer(True),
                   width=17).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(toolbar, text="在当前楼层下插入楼层", command=lambda: self.insert_layer(False),
                   width=17).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text="删除当前层", command=self.remove_layer,
                   width=9).pack(side=tk.LEFT, padx=(4, 0))

        # --- second row -------------------------------------------------
        row2 = ttk.Frame(self.root, padding=(8, 0))
        row2.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

        ttk.Label(row2, text="工具").pack(side=tk.LEFT)
        brush = ttk.Combobox(row2, textvariable=self.brush_var, values=BRUSH_LABELS,
                             state="readonly", width=11)
        brush.pack(side=tk.LEFT, padx=(2, 8))
        brush.bind("<<ComboboxSelected>>", lambda _e: self.on_brush_change())

        self.cell_kind_box = ttk.Combobox(
            row2, textvariable=self.cell_kind_var,
            values=[CELL_KIND_LABELS[k] for k in (AIR, GROUND, PORTAL, TEXT)],
            state="readonly", width=18)
        self.cell_kind_box.pack(side=tk.LEFT, padx=(0, 8))
        self.cell_kind_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_status())

        self.wall_state_box = ttk.Combobox(
            row2, textvariable=self.wall_state_var,
            values=[WALL_STATE_LABELS[s] for s in WALL_STATES],
            state="readonly", width=20)
        self.wall_state_box.pack(side=tk.LEFT, padx=(0, 8))
        self.wall_state_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_status())

        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(row2, text="楼层").pack(side=tk.LEFT)
        ttk.Button(row2, text="▲ 上一层", command=lambda: self.step_layer(1), width=9).pack(side=tk.LEFT, padx=(2, 2))
        ttk.Button(row2, text="▼ 下一层", command=lambda: self.step_layer(-1), width=9).pack(side=tk.LEFT)
        ttk.Button(row2, text="最底层", command=lambda: self.goto_layer(0), width=7).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(row2, text="最高层", command=lambda: self.goto_layer(self.maze.layer_count - 1), width=7).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(row2, text="将当前楼层上移", command=lambda: self.move_layer(1),
                   width=15).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(row2, text="将当前楼层下移", command=lambda: self.move_layer(-1),
                   width=15).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Label(row2, text="单元大小").pack(side=tk.LEFT, padx=(0, 2))
        cell_size_box = ttk.Spinbox(row2, from_=8, to=200, width=5,
                                    textvariable=self.cell_size_var)
        cell_size_box.pack(side=tk.LEFT)
        cell_size_box.bind("<Return>", self.on_cell_size_commit)
        cell_size_box.bind("<FocusOut>", self.on_cell_size_commit)
        cell_size_box.bind("<Up>", lambda _e: self.zoom(1))
        cell_size_box.bind("<Down>", lambda _e: self.zoom(-1))
        ttk.Button(row2, text="重载配置", command=self.reload_config, width=9).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row2, text="导出PNG", command=self.export_png, width=9).pack(side=tk.LEFT, padx=(4, 0))

        # --- third row: the look of the current floor -------------------
        # These five values are stored per layer inside the maze file, so each
        # floor can look different.  Enter (or leaving the box) applies them.
        self.style_row = ttk.Frame(self.root, padding=(8, 0))
        self.style_row.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

        # The view switch is packed first and flushed right, so it stays visible
        # even in a narrow window: the hint label on its left is what gets cut.
        #: View option, not part of the document: hides every other floor.
        self.only_active_check = ttk.Checkbutton(self.style_row, text="仅显示当前层",
                                                 variable=self.only_active_var,
                                                 command=self.on_only_active_change)
        self.only_active_check.pack(side=tk.RIGHT)
        ttk.Separator(self.style_row, orient=tk.VERTICAL).pack(side=tk.RIGHT, fill=tk.Y,
                                                               padx=8)

        ttk.Label(self.style_row, text="当前楼层外观").pack(side=tk.LEFT, padx=(0, 6))

        self.object_color_entry = self._style_entry("物件颜色", self.object_color_var, 10)
        self.floor_color_entry = self._style_entry("地面颜色", self.floor_color_var, 10)
        self.portal_color_entry = self._style_entry("传送门颜色", self.portal_color_var, 10)

        ttk.Label(self.style_row, text="墙宽").pack(side=tk.LEFT, padx=(8, 2))
        self.wall_width_box = ttk.Spinbox(self.style_row, from_=1, to=64, width=4,
                                          textvariable=self.wall_width_var)
        self.wall_width_box.pack(side=tk.LEFT)
        self.wall_width_box.bind("<Return>", self.on_style_commit)
        self.wall_width_box.bind("<FocusOut>", self.on_style_commit)

        #: Percentage of each cell covered by the floor of this floor.
        self.area_ratio_entry = self._style_entry("面积占比", self.area_ratio_var, 4)
        ttk.Label(self.style_row, text="%").pack(side=tk.LEFT, padx=(1, 0))

        ttk.Label(self.style_row,
                  text="（颜色 #RRGGBBAA，末两位是不透明度；回车生效）"
                  ).pack(side=tk.LEFT, padx=(8, 0))

        # --- canvas -----------------------------------------------------
        body = ttk.Frame(self.root)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(body, background=CANVAS_BACKGROUND, highlightthickness=0,
                                cursor="crosshair")
        hbar = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.canvas.xview)
        vbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        # --- status bar -------------------------------------------------
        status = ttk.Frame(self.root, padding=(8, 4))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status, textvariable=self.layer_info_var).pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.coord_var, width=26).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT, padx=(12, 0))

        self._checker = self._make_checkerboard(16)
        self._checker_photo = ImageTk.PhotoImage(self._checker)

    def _style_entry(self, label: str, variable: tk.StringVar, width: int) -> ttk.Entry:
        """Add one ``label + text box`` pair to the appearance row."""
        ttk.Label(self.style_row, text=label).pack(side=tk.LEFT, padx=(8, 2))
        entry = ttk.Entry(self.style_row, textvariable=variable, width=width)
        entry.pack(side=tk.LEFT)
        entry.bind("<Return>", self.on_style_commit)
        entry.bind("<FocusOut>", self.on_style_commit)
        return entry

    @staticmethod
    def _make_checkerboard(square: int) -> Image.Image:
        """Small two-tone tile used to visualise fully transparent cells."""
        tile = Image.new("RGBA", (square * 2, square * 2), (255, 255, 255, 255))
        draw = ImageDraw.Draw(tile)
        draw.rectangle([0, 0, square - 1, square - 1], fill=(228, 228, 228, 255))
        draw.rectangle([square, square, square * 2 - 1, square * 2 - 1], fill=(228, 228, 228, 255))
        return tile

    # ------------------------------------------------------------------
    # event bindings
    # ------------------------------------------------------------------
    def _bind_events(self) -> None:
        canvas = self.canvas
        canvas.bind("<Button-1>", self.on_left_click)
        canvas.bind("<ButtonRelease-1>", self.on_left_release)
        canvas.bind("<Button-3>", self.on_right_click)
        canvas.bind("<ButtonPress-2>", self.on_pan_start)
        canvas.bind("<B2-Motion>", self.on_pan_move)
        canvas.bind("<Motion>", self.on_motion)
        canvas.bind("<Leave>", self.on_leave)
        canvas.bind("<MouseWheel>", self.on_wheel)
        canvas.bind("<Control-MouseWheel>", self.on_zoom_wheel)
        canvas.bind("<Button-4>", self.on_wheel)      # X11
        canvas.bind("<Button-5>", self.on_wheel)
        canvas.bind("<Control-Button-4>", self.on_zoom_wheel)
        canvas.bind("<Control-Button-5>", self.on_zoom_wheel)

        root = self.root
        root.bind("<Control-s>", lambda _e: self.save_maze())
        root.bind("<Control-S>", lambda _e: self.save_maze_as())
        root.bind("<Control-o>", lambda _e: self.open_maze())
        root.bind("<Control-n>", lambda _e: self.new_maze())
        root.bind("<Next>", lambda _e: self.step_layer(1))
        root.bind("<Prior>", lambda _e: self.step_layer(-1))
        root.bind("<Home>", lambda _e: self.goto_layer(0))
        root.bind("<End>", lambda _e: self.goto_layer(self.maze.layer_count - 1))
        root.bind("<Escape>", lambda _e: self.cancel_pending_portal())
        for key in ("Left", "Right", "Up", "Down"):
            root.bind(f"<{key}>", self.on_arrow_pan)
        root.bind("z", lambda _e: self.zoom(-1))
        root.bind("x", lambda _e: self.zoom(1))
        root.bind("<Delete>", lambda _e: self.clear_active_layer())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def refresh(self, fit: bool = False, keep_view: bool = True) -> None:
        """Re-render the whole picture and rebuild every tile.

        With ``fit=True`` the cell size is chosen so the whole maze fits, unless
        the configured cell size already fits comfortably.  Use
        :meth:`refresh_dirty` after a small edit: this one redraws everything.
        """
        maze = self.maze
        if fit:
            self.fit_to_window()
        # The checkbutton is the single source of truth for the view mode.
        self.renderer.show_only_active = bool(self.only_active_var.get())
        self._dirty_cells.clear()
        self._dirty_tiles.clear()
        image = self.renderer.render(maze)
        self._rebuild_tiles(self._flatten(image))
        self.canvas.configure(scrollregion=(0, 0, image.width, image.height))
        self.refresh_status()

    # ------------------------------------------------------------------
    # tiles
    # ------------------------------------------------------------------
    def _tile_count(self) -> Tuple[int, int]:
        """Number of tile columns and rows of the current grid."""
        return (max(1, -(-self.maze.width // TILE_CELLS)),
                max(1, -(-self.maze.height // TILE_CELLS)))

    def _tile_box(self, key: Tuple[int, int]) -> Tuple[int, int, int, int]:
        """Pixel rectangle of one tile, clipped to the picture."""
        size = self.renderer.cell_size
        col, row = key
        x0 = col * TILE_CELLS * size
        y0 = row * TILE_CELLS * size
        return (x0, y0,
                min(x0 + TILE_CELLS * size, self.maze.width * size),
                min(y0 + TILE_CELLS * size, self.maze.height * size))

    def _stroke_pad(self) -> int:
        """Pixels a wall stroke can reach into the neighbouring cell."""
        widest = max((int(getattr(layer, "wall_width", 3)) for layer in self.maze.layers),
                     default=3)
        return widest // 2 + 2

    def _tiles_for_cells(self, cells) -> set:
        """Tiles that an edit of ``cells`` can change."""
        size = self.renderer.cell_size
        pad = self._stroke_pad()
        step = TILE_CELLS * size
        cols, rows = self._tile_count()
        result = set()
        for cell in cells:
            x0 = cell[0] * size - pad
            y0 = cell[1] * size - pad
            x1 = (cell[0] + 1) * size + pad
            y1 = (cell[1] + 1) * size + pad
            for col in range(max(0, x0 // step), min(cols - 1, (x1 - 1) // step) + 1):
                for row in range(max(0, y0 // step), min(rows - 1, (y1 - 1) // step) + 1):
                    result.add((col, row))
        return result

    def _set_tile(self, key: Tuple[int, int], piece: Image.Image) -> None:
        """Put a freshly drawn tile onto the canvas, reusing its Tk photo."""
        photo = self._tile_photos.get(key)
        if photo is not None and photo.width() == piece.width \
                and photo.height() == piece.height:
            # Same size: update the existing Tk photo.  Building a new one costs
            # another full upload, which is the expensive part of a redraw.
            photo.paste(piece)
            return
        if photo is not None:
            item = self._tile_items.pop(key, None)
            if item is not None:
                self.canvas.delete(item)
        photo = ImageTk.PhotoImage(piece)
        self._tile_photos[key] = photo
        box = self._tile_box(key)
        self._tile_items[key] = self.canvas.create_image(box[0], box[1], anchor=tk.NW,
                                                         image=photo)

    def _rebuild_tiles(self, flat: Image.Image) -> None:
        """Slice a whole-picture image into one tile photo each."""
        self._boards.clear()
        cols, rows = self._tile_count()
        wanted = {(col, row) for row in range(rows) for col in range(cols)}
        for key in set(self._tile_items) | set(self._tile_photos):
            if key not in wanted:
                item = self._tile_items.pop(key, None)
                if item is not None:
                    self.canvas.delete(item)
                self._tile_photos.pop(key, None)
        for key in sorted(wanted):
            box = self._tile_box(key)
            self._set_tile(key, flat.crop(box))

    def mark_cell_dirty(self, cell: Cell) -> None:
        """Remember that an edit may change the tiles around ``cell``."""
        self._dirty_cells.add(cell)

    def refresh_dirty(self) -> None:
        """Redraw only the tiles touched by the edits since the last redraw."""
        if not self._tile_photos:            # nothing built yet: full redraw
            self.refresh()
            return
        if not self._dirty_cells and not self._dirty_tiles:
            self.refresh_status()
            return
        tiles = self._tiles_for_cells(self._dirty_cells) | self._dirty_tiles
        self._dirty_cells = set()
        self._dirty_tiles = set()
        self._update_tiles(tiles)
        self.refresh_status()

    def _update_tiles(self, tiles) -> None:
        """Redraw the given tiles from the maze."""
        self.renderer.show_only_active = bool(self.only_active_var.get())
        self.renderer.begin(self.maze)       # portal arrows may cross a tile
        for key in sorted(tiles):
            box = self._tile_box(key)
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            self._set_tile(key, self._flatten(self.renderer.render_region(self.maze, box)))

    def _flatten(self, image: Image.Image) -> Image.Image:
        """Composite a render over the canvas colour and return an RGB image.

        Tk photos here carry no usable alpha, so an image that still contains
        transparent pixels is blended pixel by pixel while it is uploaded:
        measured on this machine that costs ~0.3-1.7 us per pixel and grows
        faster than the area, while an opaque image of the same size takes
        ~6 ns per pixel -- and the editor redraws on every mouse move.
        Flattening in PIL first costs ~5 ns per pixel and looks exactly the
        same, because those transparent pixels used to show the canvas
        background anyway.
        """
        if image.mode != "RGBA":
            return image.convert("RGB")
        board = self._boards.get(image.size)
        if board is None:
            board = Image.new("RGBA", image.size, _CANVAS_BACKGROUND_RGBA)
            self._boards[image.size] = board
        return Image.alpha_composite(board, image).convert("RGB")

    def fit_to_window(self) -> None:
        """Pick a cell size that makes the whole maze visible.

        The size configured in ``config.json`` is kept when it already fits
        comfortably, so opening a small maze does not blow its cells up to
        giant squares.
        """
        self.canvas.update_idletasks()
        avail_w = max(64, self.canvas.winfo_width() - 8)
        avail_h = max(64, self.canvas.winfo_height() - 8)
        ideal = int(min(avail_w / self.maze.width, avail_h / self.maze.height))
        configured = int(self.config.cell_size)
        if configured <= ideal * 1.5 and configured <= 120:
            size = max(8, configured)
        else:
            size = max(8, min(200, ideal))
        self.renderer.set_cell_size(size)
        self.cell_size_var.set(size)

    def refresh_status(self) -> None:
        maze = self.maze
        index = maze.active
        floor = maze.floor_of(index)
        layer = maze.active_layer()
        mode = "  仅显示当前层" if self.only_active_var.get() else ""
        self.layer_info_var.set(
            f"当前 {floor} 层（栈内第 {index + 1}/{maze.layer_count} 层）  "
            f"物件色 {layer.object_color}  地面色 {layer.floor_color}  "
            f"传送门色 {layer.portal_color}  墙宽 {layer.wall_width}  "
            f"面积占比 {layer.area_ratio}%{mode}"
        )
        brush = self.brush_var.get()
        if brush == BRUSH_CELL:
            kind = self._selected_kind()
            if kind == PORTAL:
                if self.pending_portal_source is None:
                    hint = "传送门：点击起点单元格"
                else:
                    hint = (f"传送门起点 {self.pending_portal_source} → "
                            "点击目的地单元格（Esc 取消）")
            elif kind == TEXT:
                hint = "左键写文本（弹窗输入），右键擦除"
            else:
                hint = f"左键涂「{self.cell_kind_var.get()}」（可按住拖动连续涂），右键擦除"
        elif brush == BRUSH_WALL:
            hint = (f"左键设「{self.wall_state_var.get()}」（Shift+左键反向，可拖动连续设置），"
                    "右键循环状态")
        else:
            hint = ""
        mark = " *未保存" if self.dirty else ""
        self.status_var.set(f"{hint}{mark}")

    # ------------------------------------------------------------------
    # coordinate helpers
    # ------------------------------------------------------------------
    def _canvas_to_cell(self, event) -> Optional[Cell]:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        cell = self.renderer.cell_at(x, y)
        if cell is None or not self.maze.contains(*cell):
            return None
        return cell

    def _canvas_to_edge(self, event) -> Optional[Tuple[Cell, Cell]]:
        """Nearest shared edge to the click, when the click is close to it."""
        size = self.renderer.cell_size
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        cell = self.renderer.cell_at(x, y)
        if cell is None or not self.maze.contains(*cell):
            return None

        cx, cy = cell
        local_x = x - cx * size
        local_y = y - cy * size
        # A shrunken floor draws its walls on the cell's inset line instead of
        # on the boundary, so the band that counts as "near this edge" follows
        # the wall inwards -- but never past 45 % of the cell, so that the
        # middle of the cell always stays clickable for the cell brush.
        from renderer import cell_bands  # local import keeps headless use cheap

        inset, _inner = cell_bands(self.maze.active_layer().area_ratio, size)
        threshold = max(3.0, min(size * 0.45, size * 0.28 + inset * 0.5))

        best: Optional[Tuple[Cell, Cell]] = None
        best_distance = threshold
        if local_x < threshold and cx > 0:  # left boundary
            distance = local_x
            if distance < best_distance:
                best_distance = distance
                best = ((cx - 1, cy), (cx, cy))
        if size - local_x < threshold and cx < self.maze.width - 1:  # right boundary
            distance = size - local_x
            if distance < best_distance:
                best_distance = distance
                best = ((cx, cy), (cx + 1, cy))
        if local_y < threshold and cy > 0:  # top boundary
            distance = local_y
            if distance < best_distance:
                best_distance = distance
                best = ((cx, cy - 1), (cx, cy))
        if size - local_y < threshold and cy < self.maze.height - 1:  # bottom boundary
            distance = size - local_y
            if distance < best_distance:
                best_distance = distance
                best = ((cx, cy), (cx, cy + 1))
        return best

    def _selected_kind(self) -> str:
        label = self.cell_kind_var.get()
        for kind, text in CELL_KIND_LABELS.items():
            if text == label:
                return kind
        return GROUND

    def _selected_wall_state(self) -> str:
        label = self.wall_state_var.get()
        for state, text in WALL_STATE_LABELS.items():
            if text == label:
                return state
        return "wall"

    # ------------------------------------------------------------------
    # mouse handling
    # ------------------------------------------------------------------
    def on_motion(self, event) -> None:
        cell = self._canvas_to_cell(event)
        if cell is None:
            self.coord_var.set("")
            if self._dragging:
                self._drag_cells.add(None)  # remember that the stroke left the grid
            return
        self._update_coord_label(cell)
        if not self._dragging:
            return
        # While the left button is held, keep painting along the stroke.
        if cell in self._drag_cells:
            return
        if not self._current_brush_is_draggable():
            return
        self._drag_cells.add(cell)
        self.apply_drag_at(event, cell)

    def on_leave(self, _event) -> None:
        self.coord_var.set("")

    def _update_coord_label(self, cell: Cell) -> None:
        data = self.maze.get_cell(*cell)
        layer = self.maze.active_layer()
        flags = layer.open_edges(*cell)
        open_text = "".join(name for name in ("N", "E", "S", "W") if flags.get(name))
        self.coord_var.set(
            f"({cell[0]}, {cell[1]}) {CELL_KIND_LABELS.get(data.kind, data.kind)}"
            f"  通路:{open_text or '无'}"
        )

    def _current_brush_is_draggable(self) -> bool:
        """Whether the current brush may be applied repeatedly during a drag."""
        if self.brush_var.get() == BRUSH_WALL:
            return True
        return self._selected_kind() not in NON_DRAGGABLE_KINDS

    def on_left_click(self, event) -> None:
        self.canvas.focus_set()
        self._drag_cells = set()
        self._last_edge = None
        self._dragging = True
        cell = self._canvas_to_cell(event)
        if self.brush_var.get() == BRUSH_WALL:
            edge = self._canvas_to_edge(event)
            if edge is None:
                return
            if cell is not None:
                self._drag_cells.add(cell)
            self._apply_wall_brush(edge[0], edge[1], event)
            return
        if cell is None:
            return
        self._drag_cells.add(cell)
        kind = self._selected_kind()
        if kind in NON_DRAGGABLE_KINDS:
            # A portal needs a second click, a text cell a dialog: no stroke.
            self._dragging = False
            if kind == PORTAL:
                self.handle_portal_click(cell)
            else:
                self.paint_cell(cell, kind)
            return
        self.paint_cell(cell, kind, refresh=False)
        self.refresh_dirty()

    def on_left_release(self, _event) -> None:
        self._dragging = False
        self._drag_cells = set()
        self._last_edge = None

    def apply_drag_at(self, event, cell: Cell) -> None:
        """Apply the current brush to ``cell``, bridging skipped cells.

        Fast mouse movement delivers events several cells apart, so the cells
        (and wall edges) in between are filled in to keep the stroke solid.
        """
        if self.brush_var.get() == BRUSH_WALL:
            edge = self._canvas_to_edge(event)
            if edge is None:
                return
            self._apply_wall_brush(edge[0], edge[1], event)
            return

        kind = self._selected_kind()
        previous = self._last_drag_cell()
        cells = self._cells_between(previous, cell) if previous is not None else [cell]
        for intermediate in cells:
            self._drag_cells.add(intermediate)
            self.paint_cell(intermediate, kind, refresh=False)
        self.refresh_dirty()

    def _last_drag_cell(self) -> Optional[Cell]:
        """Most recent real cell of the stroke (``None`` if the grid was left)."""
        for entry in reversed(list(self._drag_cells)):
            if entry is not None:
                return entry
        return None

    @staticmethod
    def _cells_between(start: Cell, end: Cell) -> list:
        """Every cell on the 4-connected path from ``start`` to ``end``."""
        if start is None or start == end:
            return [end]
        result = []
        x, y = start
        while x != end[0] or y != end[1]:
            if x != end[0]:
                x += 1 if end[0] > x else -1
            elif y != end[1]:
                y += 1 if end[1] > y else -1
            result.append((x, y))
        return result

    def _edges_between(self, start: Cell, end: Cell) -> list:
        """Every shared edge crossed by the 4-connected path ``start`` -> ``end``.

        Horizontal steps cross the left/right boundary of a cell, vertical steps
        the top/bottom one, so the wall brush can follow a fast drag without
        leaving gaps.
        """
        edges = []
        x, y = start
        while x != end[0] or y != end[1]:
            if x != end[0]:
                nx = x + (1 if end[0] > x else -1)
                edges.append(edge_key((x, y), (nx, y)))
                x = nx
            else:
                ny = y + (1 if end[1] > y else -1)
                edges.append(edge_key((x, y), (x, ny)))
                y = ny
        return edges

    def _apply_wall_brush(self, a: Cell, b: Cell, event) -> None:
        """Set one edge with the wall brush, bridging a fast drag."""
        previous = self._last_edge
        if previous is not None:
            for key in self._edges_between(previous, a):
                self._set_edge_by_key(key, event)
        self._set_edge_by_key(edge_key(a, b), event)
        self._last_edge = a
        self.refresh_dirty()

    def _set_edge_by_key(self, key, event) -> None:
        low, high = edge_cells(key)
        if event.state & 0x0001:  # Shift -> flip the direction of a one-way gate
            self.maze.active_layer().set_edge(low, high, False, True)
        else:
            self.maze.active_layer().set_edge_state(low, high, self._selected_wall_state())
        # A wall changes the ink of both cells that share the edge (and, through
        # their wall strokes, possibly the pixel row of the neighbour).
        self.mark_cell_dirty(low)
        self.mark_cell_dirty(high)
        self.mark_dirty()

    def on_right_click(self, event) -> None:
        """Erase (cell brush) / cycle state (wall brush)."""
        brush = self.brush_var.get()
        if brush == BRUSH_WALL:
            edge = self._canvas_to_edge(event)
            if edge is None:
                return
            a, b = edge
            current = self.maze.active_layer().edge_state(a, b)
            if current == (True, True):
                state = "wall"
            elif current == (False, False):
                state = "oneway"
            else:
                state = "open"
            self.set_edge(a, b, state)
            self.wall_state_var.set(WALL_STATE_LABELS[state])
            self.refresh_status()
            return

        cell = self._canvas_to_cell(event)
        if cell is None:
            return
        self.paint_cell(cell, AIR)

    # -- edits -----------------------------------------------------------
    def paint_cell(self, cell: Cell, kind: str, refresh: bool = True) -> None:
        layer = self.maze.active_layer()
        old = layer.get_cell(*cell)
        if kind == PORTAL:
            # Interactive portal creation goes through handle_portal_click; this
            # path keeps an existing destination when one is already set.
            target = old.portal if old.kind == PORTAL else None
            layer.set_cell(cell[0], cell[1], CellData(kind=PORTAL, portal=target))
        elif kind == TEXT:
            text = old.text if old.kind == TEXT else ""
            if not text:
                text = simpledialog.askstring("文本内容", f"单元 ({cell[0]}, {cell[1]}) 的文本：",
                                              parent=self.root) or ""
                if not text:
                    return
            layer.set_cell(cell[0], cell[1], CellData(kind=TEXT, text=text))
        elif kind == AIR:
            layer.clear_cell(cell[0], cell[1])
        else:
            layer.set_cell(cell[0], cell[1], CellData(kind=kind))
        # The cell itself plus the ink of a portal arrow that appeared or went.
        self._mark_arrow_dirty(cell, old)
        self._mark_arrow_dirty(cell, layer.get_cell(*cell))
        self.mark_cell_dirty(cell)
        self.mark_dirty()
        if refresh:
            self.refresh_dirty()

    def _mark_arrow_dirty(self, cell: Cell, data: Optional[CellData]) -> None:
        """Mark the tiles a portal arrow of ``data`` paints ink on.

        An arrow runs from cell centre to cell centre, so it may cross tiles far
        away from its own cell; when one is added or removed all of them have to
        be repainted.
        """
        if data is None or data.kind != PORTAL:
            return
        box = self.renderer.arrow_box(self.maze.active_layer(), cell, data)
        size = self.renderer.cell_size
        step = TILE_CELLS * size
        cols, rows = self._tile_count()
        col0 = max(0, int(box[0] // step))
        row0 = max(0, int(box[1] // step))
        col1 = min(cols - 1, int(box[2] // step))
        row1 = min(rows - 1, int(box[3] // step))
        for col in range(col0, col1 + 1):
            for row in range(row0, row1 + 1):
                self._dirty_tiles.add((col, row))

    def ask_portal_target(self, source: Cell) -> Optional[Cell]:
        """Ask for a portal destination by coordinates.

        Kept for scripted use; the interactive brush uses two mouse clicks
        (:meth:`handle_portal_click`) instead.
        """
        answer = simpledialog.askstring(
            "传送目的地",
            f"传送门起点 ({source[0]}, {source[1]})\n"
            "请输入目的地单元格坐标（同层），格式 x,y：",
            parent=self.root,
        )
        if not answer:
            return None
        try:
            parts = answer.replace("，", ",").split(",")
            target = (int(parts[0].strip()), int(parts[1].strip()))
        except (ValueError, IndexError):
            messagebox.showwarning("坐标无效", "请输入形如 3,5 的整数坐标。", parent=self.root)
            return None
        if not self.maze.contains(*target):
            messagebox.showwarning("超出范围",
                                   f"坐标必须在 0..{self.maze.width - 1} / 0..{self.maze.height - 1} 之间。",
                                   parent=self.root)
            return None
        return target

    def handle_portal_click(self, cell: Cell) -> None:
        """Two-click portal creation with the 单向传送门 brush.

        The first click sets the source cell (shown as a ring until the target
        is known), the second click sets the destination and draws the arrow.
        Clicking the same cell again, or pressing Esc, cancels.
        """
        layer = self.maze.active_layer()
        source = self.pending_portal_source
        if source is None:
            self.pending_portal_source = cell
            old = layer.get_cell(*cell)
            layer.set_cell(cell[0], cell[1], CellData(kind=PORTAL, portal=None))
            self._mark_arrow_dirty(cell, old)
            self.mark_cell_dirty(cell)
            self.mark_dirty()
            self.refresh_dirty()
            return
        if source == cell:
            self.pending_portal_source = None
            self.refresh_status()
            return
        old = layer.get_cell(*source)
        layer.set_cell(source[0], source[1], CellData(kind=PORTAL, portal=cell))
        self._mark_arrow_dirty(source, old)
        self._mark_arrow_dirty(source, layer.get_cell(*source))
        self.mark_cell_dirty(source)
        self.mark_cell_dirty(cell)
        self.pending_portal_source = None
        self.mark_dirty()
        self.refresh_dirty()

    def cancel_pending_portal(self) -> None:
        if self.pending_portal_source is not None:
            self.pending_portal_source = None
            self.refresh_status()

    def set_edge(self, a: Cell, b: Cell, state: str, reverse: bool = False) -> None:
        layer = self.maze.active_layer()
        if state == "oneway" and reverse:
            layer.set_edge(a, b, False, True)
        else:
            layer.set_edge_state(a, b, state)
        self.mark_cell_dirty(a)
        self.mark_cell_dirty(b)
        self.mark_dirty()
        self.refresh_dirty()

    def on_brush_change(self) -> None:
        brush = self.brush_var.get()
        self.cell_kind_box.configure(state="readonly" if brush == BRUSH_CELL else "disabled")
        self.wall_state_box.configure(state="readonly" if brush == BRUSH_WALL else "disabled")
        if brush != BRUSH_CELL or self._selected_kind() != PORTAL:
            self.pending_portal_source = None
        self.refresh_status()

    # ------------------------------------------------------------------
    # view helpers
    # ------------------------------------------------------------------
    def on_pan_start(self, event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def on_pan_move(self, event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def on_wheel(self, event) -> None:
        delta = 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else -1
        self.canvas.yview_scroll(-delta * 2, "units")

    def on_zoom_wheel(self, event) -> None:
        delta = 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else -1
        self.zoom(delta)

    def on_arrow_pan(self, event) -> None:
        step = 1
        if event.keysym == "Left":
            self.canvas.xview_scroll(-step, "units")
        elif event.keysym == "Right":
            self.canvas.xview_scroll(step, "units")
        elif event.keysym == "Up":
            self.canvas.yview_scroll(-step, "units")
        elif event.keysym == "Down":
            self.canvas.yview_scroll(step, "units")

    def zoom(self, delta: int) -> None:
        size = max(8, min(200, self.renderer.cell_size + delta * 2))
        if size == self.renderer.cell_size:
            return
        self.renderer.set_cell_size(size)
        self.cell_size_var.set(size)
        self.refresh()

    def on_cell_size_change(self) -> None:
        try:
            size = int(self.cell_size_var.get())
        except (tk.TclError, ValueError):
            return
        self.renderer.set_cell_size(size)
        self.refresh()

    def on_cell_size_commit(self, _event=None) -> None:
        """Apply the typed cell size (Enter or focus loss) and redraw at once."""
        try:
            size = int(float(self.cell_size_var.get()))
        except (tk.TclError, ValueError):
            self.cell_size_var.set(self.renderer.cell_size)
            return
        size = max(8, min(200, size))
        self.cell_size_var.set(size)
        if size == self.renderer.cell_size:
            return
        self.renderer.set_cell_size(size)
        self.refresh()

    # ------------------------------------------------------------------
    # appearance of the current floor
    # ------------------------------------------------------------------
    def sync_style_fields(self) -> None:
        """Show the current floor's colours, wall width and area ratio."""
        layer = self.maze.active_layer()
        self.object_color_var.set(layer.object_color)
        self.floor_color_var.set(layer.floor_color)
        self.portal_color_var.set(layer.portal_color)
        self.wall_width_var.set(str(layer.wall_width))
        self.area_ratio_var.set(str(layer.area_ratio))

    def on_style_commit(self, _event=None) -> str:
        """Apply the five appearance boxes to the current floor and redraw."""
        layer = self.maze.active_layer()
        object_color = normalise_color(self.object_color_var.get(), layer.object_color)
        floor_color = normalise_color(self.floor_color_var.get(), layer.floor_color)
        portal_color = normalise_color(self.portal_color_var.get(), layer.portal_color)
        wall_width = normalise_wall_width(self.wall_width_var.get(), layer.wall_width)
        area_ratio = normalise_area_ratio(self.area_ratio_var.get(), layer.area_ratio)

        changed = (object_color != layer.object_color
                   or floor_color != layer.floor_color
                   or portal_color != layer.portal_color
                   or wall_width != layer.wall_width
                   or area_ratio != layer.area_ratio)
        layer.object_color = object_color
        layer.floor_color = floor_color
        layer.portal_color = portal_color
        layer.wall_width = wall_width
        layer.area_ratio = area_ratio
        # Echo the normalised values back into the boxes.
        self.sync_style_fields()
        if changed:
            self.mark_dirty()
            self.refresh()
        return "break"

    def apply_layer_style(self, object_color=None, floor_color=None,
                          portal_color=None, wall_width=None,
                          area_ratio=None) -> None:
        """Set the current floor's appearance programmatically (used by tests)."""
        if object_color is not None:
            self.object_color_var.set(str(object_color))
        if floor_color is not None:
            self.floor_color_var.set(str(floor_color))
        if portal_color is not None:
            self.portal_color_var.set(str(portal_color))
        if wall_width is not None:
            self.wall_width_var.set(str(wall_width))
        if area_ratio is not None:
            self.area_ratio_var.set(str(area_ratio))
        self.on_style_commit()

    def on_only_active_change(self) -> None:
        """Redraw after the 仅显示当前层 box was ticked or unticked."""
        self.refresh()

    # ------------------------------------------------------------------
    # maze level commands
    # ------------------------------------------------------------------
    def mark_dirty(self) -> None:
        self.dirty = True
        self.refresh_status()

    def apply_size(self) -> None:
        try:
            width, height = int(self.width_var.get()), int(self.height_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("无效尺寸", "宽高必须是整数。", parent=self.root)
            return
        if width < 1 or height < 1:
            messagebox.showwarning("无效尺寸", "宽高至少为 1。", parent=self.root)
            return
        if width * height * self.maze.layer_count > 4_000_000:
            if not messagebox.askyesno("尺寸较大", "单元总数很大，可能较慢。继续？", parent=self.root):
                return
        self.maze.resize(width, height)
        self.mark_dirty()
        self.refresh(fit=True)

    def insert_layer(self, above: bool) -> None:
        """Insert an empty layer directly above or below the current floor.

        The new layer becomes the current one and starts with the default
        appearance (黑物件 / 白地面 / 品红传送门 / 墙宽 3 / 面积占比 100).
        Floor numbers are recomputed from the stack, so inserting in the middle
        renumbers the floors above.
        """
        maze = self.maze
        index = maze.active + (1 if above else 0)
        maze.layers.insert(index, Layer(maze.width, maze.height))
        maze.active = index
        self.layer_count_var.set(maze.layer_count)
        self.pending_portal_source = None
        self.sync_style_fields()
        self.mark_dirty()
        self.refresh()

    def remove_layer(self) -> None:
        maze = self.maze
        if maze.layer_count <= 1:
            messagebox.showinfo("无法删除", "至少需要保留一层。", parent=self.root)
            return
        floor = maze.floor_of(maze.active)
        if not messagebox.askyesno("删除楼层", f"确定删除 {floor} 层（栈内第 {maze.active + 1} 层）？",
                                   parent=self.root):
            return
        maze.remove_layer(maze.active)
        self.layer_count_var.set(maze.layer_count)
        self.pending_portal_source = None
        self.sync_style_fields()
        self.mark_dirty()
        self.refresh()

    def move_layer(self, delta: int) -> None:
        """Swap the current floor with its neighbour and redraw immediately."""
        maze = self.maze
        target = maze.active + delta
        if not 0 <= target < maze.layer_count:
            return
        maze.layers[maze.active], maze.layers[target] = \
            maze.layers[target], maze.layers[maze.active]
        maze.active = target
        self.pending_portal_source = None
        self.sync_style_fields()
        self.mark_dirty()
        self.refresh()

    def step_layer(self, delta: int) -> None:
        self.goto_layer(self.maze.active + delta)

    def goto_layer(self, index: int) -> None:
        index = max(0, min(self.maze.layer_count - 1, index))
        if index == self.maze.active:
            return
        self.maze.active = index
        self.pending_portal_source = None
        self.sync_style_fields()
        # Without 仅显示当前层 the composite does not depend on which floor is
        # active, so switching floors needs no repaint at all.
        if self.only_active_var.get():
            self.refresh()
        else:
            self.refresh_status()

    def clear_active_layer(self) -> None:
        if not messagebox.askyesno("清空楼层", "清空当前楼层的所有单元内容与墙？", parent=self.root):
            return
        self.maze.active_layer().clear_all()
        self.mark_dirty()
        self.refresh()

    # ------------------------------------------------------------------
    # file commands
    # ------------------------------------------------------------------
    def new_maze(self) -> None:
        if not self.confirm_discard():
            return
        from tkinter import simpledialog as sd

        width = sd.askinteger("新建迷宫", "宽（单元数）：", initialvalue=self.maze.width,
                              minvalue=1, maxvalue=512, parent=self.root)
        if width is None:
            return
        height = sd.askinteger("新建迷宫", "高（单元数）：", initialvalue=self.maze.height,
                               minvalue=1, maxvalue=512, parent=self.root)
        if height is None:
            return
        layers = sd.askinteger("新建迷宫", "层数：", initialvalue=self.maze.layer_count,
                               minvalue=1, maxvalue=64, parent=self.root)
        if layers is None:
            return
        self.maze = Maze(width, height, layers)
        self.width_var.set(width)
        self.height_var.set(height)
        self.layer_count_var.set(layers)
        self.path = None
        self.dirty = False
        self.pending_portal_source = None
        self.sync_style_fields()
        self.refresh(fit=True)

    def open_maze(self) -> None:
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开迷宫", parent=self.root,
            filetypes=[("迷宫 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            maze = Maze.load(path)
        except ValueError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)
            return
        self.maze = maze
        self.path = path
        self.dirty = False
        self.width_var.set(maze.width)
        self.height_var.set(maze.height)
        self.layer_count_var.set(maze.layer_count)
        self.pending_portal_source = None
        self.sync_style_fields()
        self.refresh(fit=True)
        self.root.title(f"{APP_TITLE} — {os.path.basename(path)}")

    def save_maze(self) -> None:
        if not self.path:
            self.save_maze_as()
            return
        try:
            self.maze.save(self.path)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.dirty = False
        self.refresh_status()

    def save_maze_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存迷宫", parent=self.root, defaultextension=".json",
            initialfile=os.path.basename(self.path) if self.path else "maze.json",
            filetypes=[("迷宫 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        self.path = path
        self.save_maze()
        self.root.title(f"{APP_TITLE} — {os.path.basename(path)}")

    def export_png(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出 PNG", parent=self.root, defaultextension=".png",
            initialfile="maze.png", filetypes=[("PNG 图片", "*.png")])
        if not path:
            return
        image = self.renderer.render_on_checkerboard(self.maze)
        try:
            image.save(path)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.status_var.set(f"已导出 {path}")

    def reload_config(self) -> None:
        self.config = Config.load(default_config_path())
        self.renderer.reload_config(self.config)
        self.refresh()

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "未保存的修改", "当前迷宫有未保存的修改，是否先保存？", parent=self.root)
        if answer is None:
            return False
        if answer:
            self.save_maze()
            return not self.dirty
        return True

    def on_close(self) -> None:
        if self.confirm_discard():
            self.root.destroy()


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config = Config.load()

    maze: Maze
    path: Optional[str] = None
    if argv and argv[0] not in ("--sample",):
        path = argv[0]
        try:
            maze = Maze.load(path)
        except ValueError as exc:
            print(f"无法打开 {path}: {exc}", file=sys.stderr)
            return 2
    else:
        maze = build_sample_maze()

    root = tk.Tk()
    app = MazeEditorApp(root, maze, config, path)
    if path:
        root.title(f"{APP_TITLE} — {os.path.basename(path)}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
