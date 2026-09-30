"""Smoke test for the Tkinter GUI (runs headless-ish: the window is withdrawn).

    python tests/test_gui.py

It drives the editor programmatically: paint cells, cycle wall states, create a
portal, switch floors, save/load, and finally checks that the canvas really
holds a rendered image.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (  # noqa: E402
    BRUSH_CELL,
    BRUSH_LABELS,
    BRUSH_WALL,
    TILE_CELLS,
    WALL_STATE_LABELS,
    MazeEditorApp,
)
from config import Config  # noqa: E402
from maze import (  # noqa: E402
    AIR,
    CELL_KIND_LABELS,
    GROUND,
    PORTAL,
    TEXT,
    CellData,
    Maze,
)


class FakeEvent:
    """Stand-in for a Tk event with canvas coordinates and modifier state."""

    def __init__(self, x: int, y: int, state: int = 0) -> None:
        self.x = x
        self.y = y
        self.state = state
        self.delta = 0
        self.num = 0
        self.keysym = ""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def widget_texts(widget) -> list:
    """Every visible ``text``/``values`` string in the widget tree."""
    texts = []
    try:
        texts.append(str(widget.cget("text")))
    except tk.TclError:
        pass
    try:
        values = widget.cget("values")
        texts.extend(str(v) for v in values)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        texts.extend(widget_texts(child))
    return texts


def find_widget(widget, predicate):
    """First widget in the tree matching ``predicate``, or ``None``."""
    if predicate(widget):
        return widget
    for child in widget.winfo_children():
        found = find_widget(child, predicate)
        if found is not None:
            return found
    return None


def check_toolbar_labels(app: MazeEditorApp) -> None:
    """The toolbar must offer exactly the controls asked for."""
    texts = widget_texts(app.root)
    joined = " | ".join(texts)

    for wanted in ("在当前楼层上插入楼层", "在当前楼层下插入楼层", "删除当前层",
                   "将当前楼层上移", "将当前楼层下移", "单元大小", "上一层", "下一层"):
        check(wanted in joined, f"the toolbar should offer {wanted!r}, got: {joined}")

    for removed in ("应用层数", "适应窗口", "传送门连线", "＋层", "－层", "删除层", "上移", "下移", "格子"):
        # Compare whole labels: "上移" is a substring of "将当前楼层上移".
        check(removed not in texts, f"the toolbar must no longer offer {removed!r}")

    # 单元内容 still offers all four cell kinds; 墙/连通 keeps its three states.
    for kind_label in CELL_KIND_LABELS.values():
        check(kind_label in joined, f"the cell kind {kind_label!r} must be selectable")
    for state_label in ("连通", "墙", "单向板"):
        check(state_label in joined, f"the wall state {state_label!r} must be selectable")

    # Only two brushes remain.
    check(BRUSH_LABELS == (BRUSH_CELL, BRUSH_WALL), "only 单元内容 and 墙 may remain")

    # The 仅显示当前层 view switch lives in the appearance row.
    check("仅显示当前层" in joined, "the toolbar should offer the 仅显示当前层 checkbox")

    # 层数 is a plain read-only label now, not an editable spinbox.
    check(isinstance(app.layer_count_label, ttk.Label),
          "层数 must be a Label, not a Spinbox")
    check(not isinstance(app.layer_count_label, ttk.Spinbox),
          "层数 must not be an input with increment buttons")
    check(app.layer_count_var.get() == app.maze.layer_count,
          "the label must show the real stack height")

    # 单元大小 must stay editable, and committing it redraws at once.
    box = find_widget(app.root, lambda w: isinstance(w, ttk.Spinbox)
                      and str(w.cget("textvariable")) == str(app.cell_size_var))
    check(box is not None, "the 单元大小 spinbox must exist")
    check(str(box.cget("state")) != "readonly", "单元大小 must stay editable")
    app.cell_size_var.set("33")
    app.on_cell_size_commit()
    check(app.renderer.cell_size == 33, f"Enter must apply the size, got {app.renderer.cell_size}")
    app.cell_size_var.set("999")          # out of range: clamped, not rejected
    app.on_cell_size_commit()
    check(app.renderer.cell_size == 200, "the size must be clamped to 200")
    app.cell_size_var.set("40")
    app.on_cell_size_commit()

    # The third row edits the look of the current floor.
    for wanted in ("物件颜色", "地面颜色", "传送门颜色", "墙宽", "面积占比"):
        check(wanted in joined, f"the appearance row should offer {wanted!r}")
    for entry in (app.object_color_entry, app.floor_color_entry, app.portal_color_entry,
                  app.area_ratio_entry):
        check(isinstance(entry, ttk.Entry), "the appearance boxes must be text boxes")
    check(isinstance(app.wall_width_box, ttk.Spinbox), "墙宽 must be set in a box")

    # No underground numbering anywhere in the UI text.
    for word in ("地下", "地下室", "负层"):
        check(word not in joined, f"the UI must not mention {word!r}")


def check_layer_appearance(app: MazeEditorApp) -> None:
    """Typed-in colours and wall width apply to the current floor at once."""
    maze = app.maze
    layer = maze.active_layer()

    # The boxes show the current floor's values on startup.
    check(app.object_color_var.get() == layer.object_color,
          "the boxes must show the current floor's object colour")
    check(app.wall_width_var.get() == str(layer.wall_width),
          "the wall width box must show the current floor's width")
    check(app.area_ratio_var.get() == str(layer.area_ratio) == "100",
          "the area ratio box must show the current floor's ratio")

    # Enter applies them to this layer, and marks the document dirty.
    app.dirty = False
    app.object_color_var.set("#112233FF")
    app.floor_color_var.set("#010203FF")
    app.portal_color_var.set("00ff0080")          # no '#', 8 digits: 50 % opacity
    app.wall_width_var.set("5")
    app.area_ratio_var.set("60")
    app.on_style_commit()
    check(layer.object_color == "#112233FF", f"got {layer.object_color}")
    check(layer.floor_color == "#010203FF", f"got {layer.floor_color}")
    check(layer.portal_color == "#00FF0080", f"got {layer.portal_color}")
    check(app.portal_color_var.get() == "#00FF0080",
          "the opacity of a colour must be echoed back as well")
    check(layer.wall_width == 5, f"got {layer.wall_width}")
    check(layer.area_ratio == 60, f"got {layer.area_ratio}")
    check(app.dirty, "changing the look must mark the document dirty")
    check(app._tile_photos, "committing must redraw the canvas")

    # A six digit colour counts as fully opaque, and is widened in the box.
    app.portal_color_var.set("#00FF00")
    app.on_style_commit()
    check(layer.portal_color == "#00FF00FF", f"got {layer.portal_color}")
    check(app.portal_color_var.get() == "#00FF00FF", "the box shows the 8 digit form")

    # A broken colour, width or ratio keeps the previous value and is echoed back.
    app.object_color_var.set("nope")
    app.wall_width_var.set("x")
    app.area_ratio_var.set("nope")
    app.on_style_commit()
    check(layer.object_color == "#112233FF", "a broken colour must keep the old value")
    check(layer.wall_width == 5, "a broken width must keep the old value")
    check(layer.area_ratio == 60, "a broken area ratio must keep the old value")
    check(app.object_color_var.get() == "#112233FF", "the box shows the value in force")
    check(app.area_ratio_var.get() == "60", "the ratio box shows the value in force")
    app.wall_width_var.set("999")
    app.on_style_commit()
    check(layer.wall_width == 64, "the wall width must be clamped to 64")
    app.area_ratio_var.set("999")
    app.on_style_commit()
    check(layer.area_ratio == 100, "the area ratio must be clamped to 100")
    app.area_ratio_var.set("-5")
    app.on_style_commit()
    check(layer.area_ratio == 0, "the area ratio must be clamped to 0")
    app.area_ratio_var.set("60")
    app.on_style_commit()
    check(layer.area_ratio == 60, "and it can be set back")

    # The look is per floor: switching floors swaps the boxes' contents.
    app.goto_layer(1)
    other = maze.active_layer()
    check(other is not layer, "goto_layer must switch the active layer")
    check(app.object_color_var.get() == other.object_color == "#000000FF",
          "another floor keeps its own (default) colour")
    check(app.wall_width_var.get() == str(other.wall_width) == "3",
          "and its own wall width")
    check(app.area_ratio_var.get() == str(other.area_ratio) == "100",
          "and its own (default) area ratio")
    app.goto_layer(0)
    check(app.object_color_var.get() == "#112233FF", "coming back shows the edited values")
    check(app.area_ratio_var.get() == "60", "and the edited area ratio")

    # A newly inserted floor starts from the documented defaults.
    app.insert_layer(True)
    fresh = maze.active_layer()
    check(fresh.object_color == "#000000FF", f"got {fresh.object_color}")
    check(fresh.floor_color == "#FFFFFFFF", f"got {fresh.floor_color}")
    check(fresh.portal_color == "#FF00FFFF", f"got {fresh.portal_color}")
    check(fresh.wall_width == 3, f"got {fresh.wall_width}")
    check(fresh.area_ratio == 100, f"got {fresh.area_ratio}")
    check(app.object_color_var.get() == "#000000FF", "the boxes follow the new floor")
    check(app.area_ratio_var.get() == "100", "including the area ratio box")

    # Round-trip: the values are stored in the maze file.
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "styles.json")
        app.maze.active = 0
        app.maze.save(path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        check(raw["layers"][0]["object_color"] == "#112233FF", "saved in the maze file")
        check(raw["layers"][0]["wall_width"] == 64, "wall width is saved per layer")
        check(raw["layers"][0]["area_ratio"] == 60, "area ratio is saved per layer")
        check("object_color_group" not in raw, "no global colour group in the file")
        reloaded = Maze.load(path)
    check(reloaded.layer(0).object_color == "#112233FF", "reloaded per-layer colour")
    check(reloaded.layer(0).wall_width == 64, "reloaded per-layer wall width")
    check(reloaded.layer(0).area_ratio == 60, "reloaded per-layer area ratio")


def check_only_active_box(app: MazeEditorApp) -> None:
    """The 仅显示当前层 checkbox hides every other floor and redraws."""
    check(app.only_active_var.get() is False, "仅显示当前层 must start unchecked")
    check(isinstance(app.only_active_check, ttk.Checkbutton),
          "仅显示当前层 must be a checkbox")
    check(app.renderer.show_only_active is False, "the view starts with every floor")

    maze = app.maze
    maze.active = 0
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    maze.layer(0).floor_color = "#FF0000FF"
    maze.layer(1).set_cell(0, 0, CellData(kind=GROUND))
    maze.layer(1).floor_color = "#0000FFFF"
    # The floors above must not paint over the cell we are looking at.
    for index in range(2, maze.layer_count):
        maze.layer(index).clear_cell(0, 0)
    app.refresh()

    size = app.renderer.cell_size
    centre = (size // 2, size // 2)
    composited = app.renderer.render(maze, maze.active).getpixel(centre)
    check(composited[:3] == (0, 0, 255), f"unticked: both floors are composited, got {composited}")

    app.only_active_var.set(True)
    app.on_only_active_change()          # what the checkbox command calls
    check(app.renderer.show_only_active is True, "ticking it must reach the renderer")
    alone = app.renderer.render(maze, maze.active).getpixel(centre)
    check(alone[:3] == (255, 0, 0), f"ticked: only the active floor is drawn, got {alone}")
    check(alone[3] == 255, "the active floor keeps its own colour")
    check(app.layer_info_var.get().rstrip().endswith("仅显示当前层"),
          f"the status bar must say so, got {app.layer_info_var.get()!r}")

    app.only_active_var.set(False)
    app.on_only_active_change()
    check(app.renderer.show_only_active is False, "unticking must restore the composite")
    check(app.renderer.render(maze, maze.active).getpixel(centre)[:3] == (0, 0, 255),
          "the other floors come back")


def canvas_pixel(app: MazeEditorApp, x: int, y: int) -> tuple:
    """Colour of one pixel of the picture as the canvas currently shows it.

    The pixels are read back out of the Tk photo of the tile that owns them, so
    this checks what is on screen, not what the renderer would produce.
    """
    step = TILE_CELLS * app.renderer.cell_size
    key = (x // step, y // step)
    photo = app._tile_photos[key]
    box = app._tile_box(key)
    return tuple(int(value)
                 for value in photo.tk.call(str(photo), "get", x - box[0], y - box[1]))


def check_canvas_matches_render(app: MazeEditorApp, points: list) -> None:
    """Every sampled canvas pixel must equal a freshly rendered full picture."""
    expected = app._flatten(app.renderer.render(app.maze))
    for x, y in points:
        got = canvas_pixel(app, x, y)
        check(got == expected.getpixel((x, y)),
              f"canvas pixel ({x}, {y}) is {got}, a full render says "
              f"{expected.getpixel((x, y))}")


def sample_points(app: MazeEditorApp) -> list:
    """Cell centres, cell corners (where walls are) and tile corners."""
    size = app.renderer.cell_size
    points = []
    for cy in range(app.maze.height):
        for cx in range(app.maze.width):
            points.append((cx * size + size // 2, cy * size + size // 2))
            points.append((cx * size, cy * size))
    cols, rows = app._tile_count()
    for col in range(cols):
        for row in range(rows):
            box = app._tile_box((col, row))
            points.append((box[2] - 1, box[3] - 1))
            points.append(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
    return points


def check_canvas_photo(app: MazeEditorApp) -> None:
    """The canvas is fed opaque RGB tiles, and same-size redraws reuse them."""
    maze = app.maze
    maze.active = 0
    for index in range(maze.layer_count):          # known contents for two cells
        maze.layer(index).clear_cell(0, 0)
        maze.layer(index).clear_cell(5, 0)
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    maze.layer(0).floor_color = "#112233FF"
    app.refresh()

    size = app.renderer.cell_size
    check(app._tile_photos, "refresh() must build the tile photos")

    # The render is RGBA; what reaches Tk must be opaque RGB with the transparent
    # cells showing the canvas colour (Tk would pay ~40x more per pixel for an
    # image that still contains transparency).
    image = app.renderer.render(maze)
    check(image.mode == "RGBA", "the renderer still produces an RGBA image")
    check(image.getpixel((size // 2, size // 2))[3] == 255, "the painted cell is opaque")
    flat = app._flatten(image)
    check(flat.mode == "RGB", f"the flattened image must be RGB, got {flat.mode}")
    check(flat.size == image.size, "flattening must not resize")
    check(flat.getpixel((size // 2, size // 2)) == (0x11, 0x22, 0x33),
          f"an opaque pixel keeps its colour, got {flat.getpixel((size // 2, size // 2))}")
    background = flat.getpixel((5 * size + size // 2, size // 2))
    check(background == (0xF0, 0xF0, 0xF0),
          f"a transparent cell shows the canvas colour, got {background}")
    check(app._flatten(image).size == image.size, "the background board is reusable")

    # The small test maze fits in a single tile, which covers the picture.
    check(list(app._tile_photos) == [(0, 0)], f"got {list(app._tile_photos)}")
    photo = app._tile_photos[(0, 0)]
    check((photo.width(), photo.height()) == (maze.width * size, maze.height * size),
          f"the tile photo is {photo.width()}x{photo.height()}")
    check_canvas_matches_render(app, sample_points(app))

    # Redrawing at the same size updates the photos in place ...
    before = dict(app._tile_photos)
    app.refresh()
    check(all(app._tile_photos[key] is photo for key, photo in before.items()),
          "a same-size redraw must reuse the Tk photos")
    check_canvas_matches_render(app, sample_points(app))

    # ... while a new cell size needs photos of the new size.
    app.zoom(1)
    check(app._tile_photos[(0, 0)] is not before[(0, 0)],
          "a new cell size must build new Tk photos")
    check(app._tile_photos[(0, 0)].width() == maze.width * app.renderer.cell_size,
          f"got {app._tile_photos[(0, 0)].width()}")
    check_canvas_matches_render(app, sample_points(app))
    app.zoom(-1)


def check_tile_updates(app: MazeEditorApp) -> None:
    """An edit repaints only the tiles it touches, and the canvas stays right."""
    maze = app.maze
    maze.resize(20, 18)                 # 3 x 3 tiles of TILE_CELLS cells
    maze.active = 0
    for layer in maze.layers:
        layer.clear_all()
    app.refresh()
    size = app.renderer.cell_size
    cols, rows = app._tile_count()
    check((cols, rows) == (3, 3), f"expected a 3x3 tile grid, got {(cols, rows)}")

    drawn = []
    original = app.renderer.render_region

    def spy(render_maze, box, *args, **kwargs):
        drawn.append(box)
        return original(render_maze, box, *args, **kwargs)

    app.renderer.render_region = spy
    try:
        # A cell in the middle of a tile: exactly that tile repaints.
        app.paint_cell((2, 2), GROUND)
        check(len(drawn) == 1, f"one cell must repaint one tile, got {len(drawn)}")
        check(app._tile_box((0, 0)) == drawn[0], f"got {drawn}")
        check_canvas_matches_render(app, sample_points(app))

        # A cell straddling a tile boundary repaints both neighbours.
        drawn.clear()
        app.paint_cell((TILE_CELLS, 2), GROUND)          # first cell of tile (1, 0)
        check(len(drawn) == 2, f"a boundary cell must repaint two tiles, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))

        # Walls belong to two cells, so they repaint the same way.
        drawn.clear()
        app.set_edge((2, 2), (2, 3), "wall")
        check(len(drawn) == 1, f"a wall inside one tile repaints one tile, got {len(drawn)}")
        check(maze.passable((2, 2), (2, 3)) is False, "the wall must be set")
        check_canvas_matches_render(app, sample_points(app))

        # A portal arrow crosses tiles: all of them have to be repainted, and
        # the arrow must really be on the canvas (it is drawn last).
        drawn.clear()
        app.pending_portal_source = None
        app.handle_portal_click((1, 1))
        app.handle_portal_click((17, 15))
        check(maze.get_cell(1, 1).portal == (17, 15), "the portal must point at the target")
        check(len(drawn) >= 3, f"a long arrow must repaint every tile it crosses, "
                               f"got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))

        # Erasing the portal takes the arrow away again.
        drawn.clear()
        app.pending_portal_source = None
        app.paint_cell((1, 1), AIR)
        check(len(drawn) >= 3, f"removing the arrow must repaint its tiles, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))

        # A portal whose destination is unknown draws a ring, and setting the
        # destination must repaint everything the arrow reaches.
        drawn.clear()
        app.pending_portal_source = None
        app.handle_portal_click((2, 10))
        check(maze.get_cell(2, 10).portal is None, "the first click leaves the target open")
        check(len(drawn) == 1, f"the ring is inside one tile, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))
        drawn.clear()
        app.handle_portal_click((14, 1))
        check(len(drawn) >= 2, f"the finished arrow crosses tiles, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))

        # A drag stroke paints every cell it crosses and repaints their tiles.
        drawn.clear()
        app.brush_var.set(BRUSH_CELL)
        app.cell_kind_var.set(CELL_KIND_LABELS[GROUND])
        app._drag_cells = set()
        app._dragging = True
        app._drag_cells.add((3, 4))
        app.paint_cell((3, 4), GROUND, refresh=False)
        app.apply_drag_at(FakeEvent(0, 0), (10, 4))
        app._dragging = False
        app._drag_cells = set()
        check(maze.get_cell(10, 4).kind == GROUND, "the drag must paint its last cell")
        check(len(drawn) >= 2, f"a stroke across tiles repaints them, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))

        # Structural changes rebuild every tile.
        drawn.clear()
        app.apply_layer_style(floor_color="#123456FF")
        check(len(drawn) == 0, "a full refresh must not go through render_region")
        check_canvas_matches_render(app, sample_points(app))
        check(canvas_pixel(app, 2 * size + size // 2, 2 * size + size // 2)
              == (0x12, 0x34, 0x56), "the new floor colour must reach the canvas")

        # 仅显示当前层 goes through the same full path ...
        drawn.clear()
        app.only_active_var.set(True)
        app.on_only_active_change()
        check(len(drawn) == 0, "ticking the box redraws everything")
        check(app.renderer.show_only_active is True, "the renderer follows the box")
        check_canvas_matches_render(app, sample_points(app))

        # ... and an edit while it is ticked only repaints its own tile.
        drawn.clear()
        app.paint_cell((2, 2), AIR)
        check(len(drawn) == 1, f"one tile while 仅显示当前层, got {len(drawn)}")
        check_canvas_matches_render(app, sample_points(app))
        app.only_active_var.set(False)
        app.on_only_active_change()

        # The pixel comparison above must really be able to see a stale tile:
        # change a cell behind the app's back, so nothing is marked dirty.
        maze.active_layer().set_cell(6, 6, CellData(kind=GROUND))
        app.refresh_dirty()
        stale = False
        try:
            check_canvas_matches_render(app, sample_points(app))
        except AssertionError:
            stale = True
        check(stale, "the canvas check must notice a tile that was never repainted")
        app.refresh()
    finally:
        app.renderer.render_region = original

    maze.resize(6, 5)
    app.refresh()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - no display available
        print(f"SKIP  no display available: {exc}")
        return 0

    root.geometry("900x600")
    root.deiconify()
    root.update()
    root.update_idletasks()

    config = Config({"cell_size": 40})
    maze = Maze(6, 5, layers=3)
    app = MazeEditorApp(root, maze, config)
    root.update()
    root.withdraw()  # keep the window off screen while the test drives it

    # The configured cell size is kept whenever it fits the viewport.
    app.cell_size_var.set(40)
    app.on_cell_size_change()
    size = app.renderer.cell_size
    check(size == 40, f"a fitting configured cell size must be kept, got {size}")

    # --- toolbar wording ------------------------------------------------
    check_toolbar_labels(app)

    # --- per-floor appearance boxes --------------------------------------
    check_layer_appearance(app)

    # --- drag painting: one press, a stroke, one release -----------------
    app.brush_var.set(BRUSH_CELL)
    app.cell_kind_var.set(CELL_KIND_LABELS[GROUND])
    app.on_left_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.get_cell(2, 1).kind == GROUND, "left click should paint a ground cell")
    check(app.dirty, "painting must mark the document dirty")

    # Drag across row 1 from x=2 to x=4, skipping (3, 1) on purpose.
    app.on_motion(FakeEvent(int(4.5 * size), int(1.5 * size)))
    check(maze.get_cell(3, 1).kind == GROUND, "the drag must fill the skipped cell")
    check(maze.get_cell(4, 1).kind == GROUND, "the drag must paint the cell under the mouse")
    app.on_left_release(FakeEvent(0, 0))
    check(not app._dragging, "releasing the button must end the stroke")

    # --- erase it with the right button -------------------------------
    app.on_right_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.get_cell(2, 1).kind == AIR, "right click should erase the cell")

    # --- wall brush: the shared edge of (2,1) and (3,1) ---------------
    app.brush_var.set(BRUSH_WALL)
    app.wall_state_var.set(WALL_STATE_LABELS["wall"])
    edge_x = 3 * size + 1  # just inside the boundary between x=2 and x=3
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.passable((2, 1), (3, 1)) is False, "wall brush should block the edge")
    check(maze.passable((3, 1), (2, 1)) is False, "wall brush should block both ways")

    # Dragging the wall brush along the boundary keeps setting walls.
    app.on_motion(FakeEvent(edge_x, int(2.5 * size)))
    check(maze.active_layer().edge_state((2, 2), (3, 2)) == (False, False),
          "dragging the wall brush should keep blocking edges")
    app.on_left_release(FakeEvent(0, 0))

    # A click in the middle of a cell is too far from any edge to count.
    app.on_left_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (False, False),
          "a centre click must not touch walls")
    app.on_left_release(FakeEvent(0, 0))

    # At a smaller area ratio the wall sits on the inset line; the band that
    # counts as "near the edge" must follow it inwards ...
    from renderer import cell_bands

    app.apply_layer_style(area_ratio=30)
    inset = cell_bands(30, size)[0]
    layer = maze.active_layer()
    layer.set_edge((2, 1), (3, 1), True, True)      # start from an open edge
    app.on_left_click(FakeEvent(int(3 * size - inset), int(1.5 * size)))
    check(layer.edge_state((2, 1), (3, 1)) == (False, False),
          "at 30 % the wall on the inset line must still be clickable")
    app.on_left_release(FakeEvent(0, 0))
    # ... while the middle of the cell stays available for the cell brush.
    edges_before = maze.active_layer().to_json()["edges"]
    app.on_left_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.active_layer().to_json()["edges"] == edges_before,
          "a centre click must still not touch walls")
    app.on_left_release(FakeEvent(0, 0))
    app.apply_layer_style(area_ratio=60)  # the value check_layer_appearance left

    # One-way gate, then the same edge again via the right button cycle.
    app.wall_state_var.set(WALL_STATE_LABELS["oneway"])
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (True, False),
          "one-way gate should pass 2,1 -> 3,1")
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size), state=0x0001))  # Shift
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (False, True),
          "shift+click should flip the gate direction")
    app.on_right_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (True, True),
          "right click should cycle back to open")

    # --- portal brush: two clicks with the 单元内容 brush ----------------
    app.brush_var.set(BRUSH_CELL)
    app.cell_kind_var.set(CELL_KIND_LABELS[PORTAL])
    app.on_left_click(FakeEvent(int(1.5 * size), int(3.5 * size)))
    check(app.pending_portal_source == (1, 3), "first portal click sets the source")
    check(maze.get_cell(1, 3).kind == PORTAL, "the source cell becomes a portal")
    check(maze.get_cell(1, 3).portal is None, "the destination is unknown for now")
    app.on_left_click(FakeEvent(int(4.5 * size), int(3.5 * size)))
    portal = maze.get_cell(1, 3)
    check(portal.kind == PORTAL and portal.portal == (4, 3),
          f"second portal click sets the destination, got {portal}")
    check(app.pending_portal_source is None, "portal pending state must be cleared")

    # Esc cancels a half-finished portal.
    app.on_left_click(FakeEvent(int(0.5 * size), int(4.5 * size)))
    check(app.pending_portal_source == (0, 4), "a new source is pending")
    app.cancel_pending_portal()
    check(app.pending_portal_source is None, "Esc must cancel the pending portal")

    # --- text cell through the dialog-free path ------------------------
    maze.set_cell(5, 4, __import__("maze").CellData(kind=TEXT, text="测试文本"))
    app.refresh()

    # --- floors ---------------------------------------------------------
    check(app.renderer.render(maze, 0).size == (6 * size, 5 * size), "render size")
    app.goto_layer(2)
    check(maze.active == 2, "goto_layer should switch the active layer")
    app.step_layer(-1)
    check(maze.active == 1, "step_layer(-1) should move down one floor")
    app.step_layer(1)
    check(maze.active == 2, "step_layer(+1) should move up one floor")

    # Insert above / below the current floor, and check the floor numbers.
    before = maze.layer_count
    app.insert_layer(True)
    check(maze.layer_count == before + 1, "insert_layer(above) must grow the stack")
    check(maze.active == 3, "the newly inserted layer becomes current")
    check(maze.floor_of(3) == 4, f"the new layer should be floor 4, got {maze.floor_of(3)}")
    app.insert_layer(False)
    check(maze.active == 3, "the layer inserted below becomes current")
    check(maze.floor_of(3) == 4, "floors are renumbered from the stack position")

    # Move the current floor up and down; the view must follow immediately.
    marker = __import__("maze").CellData(kind=TEXT, text="marker")
    maze.active_layer().set_cell(0, 0, marker)
    app.move_layer(1)
    check(maze.active == 4, "moving up should raise the stack index")
    check(maze.layer(4).get_cell(0, 0).text == "marker", "the layer contents travel with it")
    app.move_layer(-1)
    check(maze.active == 3, "moving down should lower the stack index")
    check(maze.layer(3).get_cell(0, 0).text == "marker", "the layer contents travel back")
    check(app._tile_photos, "moving a floor must redraw the canvas")

    # --- canvas really holds the tiles ------------------------------------
    app.refresh(fit=True)
    root.update()
    check(app._tile_photos, "canvas should own tile photos")
    first_item = next(iter(app._tile_items.values()))
    check(app.canvas.itemcget(first_item, "image") != "", "a tile item must have a photo")
    check(app.layer_info_var.get().startswith("当前"), "status bar should show the floor")
    app.on_motion(FakeEvent(int(1.5 * size), int(1.5 * size)))
    check(app.coord_var.get().startswith("(1, 1)"), f"coord label: {app.coord_var.get()!r}")

    # --- the opaque tiles handed to Tk ------------------------------------
    check_canvas_photo(app)

    # --- incremental tile updates -----------------------------------------
    check_tile_updates(app)

    # --- zoom ------------------------------------------------------------
    app.zoom(1)
    check(app.renderer.cell_size == size + 2, "zoom should change the cell size")
    app.zoom(-1)

    # --- save / load through the app -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gui.json")
        app.path = path
        app.dirty = True
        app.save_maze()
        check(not app.dirty, "saving must clear the dirty flag")
        check(os.path.exists(path), "save_maze must write the file")

        before = app.maze.to_json()
        app.maze = Maze(2, 2, 1)
        app.maze = Maze.load(path)
        check(app.maze.to_json() == before, "the round trip must preserve the maze")

    # --- 仅显示当前层 checkbox -------------------------------------------
    check_only_active_box(app)

    root.destroy()
    print("ok    GUI smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
