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
    app.object_color_var.set("#112233")
    app.floor_color_var.set("#010203")
    app.portal_color_var.set("00ff00")            # no '#' -- must be normalised
    app.wall_width_var.set("5")
    app.area_ratio_var.set("60")
    app.on_style_commit()
    check(layer.object_color == "#112233", f"got {layer.object_color}")
    check(layer.floor_color == "#010203", f"got {layer.floor_color}")
    check(layer.portal_color == "#00FF00", f"got {layer.portal_color}")
    check(layer.wall_width == 5, f"got {layer.wall_width}")
    check(layer.area_ratio == 60, f"got {layer.area_ratio}")
    check(app.dirty, "changing the look must mark the document dirty")
    check(app._photo is not None, "committing must redraw the canvas")

    # A broken colour, width or ratio keeps the previous value and is echoed back.
    app.object_color_var.set("nope")
    app.wall_width_var.set("x")
    app.area_ratio_var.set("nope")
    app.on_style_commit()
    check(layer.object_color == "#112233", "a broken colour must keep the old value")
    check(layer.wall_width == 5, "a broken width must keep the old value")
    check(layer.area_ratio == 60, "a broken area ratio must keep the old value")
    check(app.object_color_var.get() == "#112233", "the box shows the value in force")
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
    check(app.object_color_var.get() == other.object_color == "#000000",
          "another floor keeps its own (default) colour")
    check(app.wall_width_var.get() == str(other.wall_width) == "3",
          "and its own wall width")
    check(app.area_ratio_var.get() == str(other.area_ratio) == "100",
          "and its own (default) area ratio")
    app.goto_layer(0)
    check(app.object_color_var.get() == "#112233", "coming back shows the edited values")
    check(app.area_ratio_var.get() == "60", "and the edited area ratio")

    # A newly inserted floor starts from the documented defaults.
    app.insert_layer(True)
    fresh = maze.active_layer()
    check(fresh.object_color == "#000000", f"got {fresh.object_color}")
    check(fresh.floor_color == "#FFFFFF", f"got {fresh.floor_color}")
    check(fresh.portal_color == "#FF00FF", f"got {fresh.portal_color}")
    check(fresh.wall_width == 3, f"got {fresh.wall_width}")
    check(fresh.area_ratio == 100, f"got {fresh.area_ratio}")
    check(app.object_color_var.get() == "#000000", "the boxes follow the new floor")
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
        check(raw["layers"][0]["object_color"] == "#112233", "saved in the maze file")
        check(raw["layers"][0]["wall_width"] == 64, "wall width is saved per layer")
        check(raw["layers"][0]["area_ratio"] == 60, "area ratio is saved per layer")
        check("object_color_group" not in raw, "no global colour group in the file")
        reloaded = Maze.load(path)
    check(reloaded.layer(0).object_color == "#112233", "reloaded per-layer colour")
    check(reloaded.layer(0).wall_width == 64, "reloaded per-layer wall width")
    check(reloaded.layer(0).area_ratio == 60, "reloaded per-layer area ratio")


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
    check(app._photo is not None, "moving a floor must redraw the canvas")

    # --- canvas really holds an image -----------------------------------
    app.refresh(fit=True)
    root.update()
    check(app._image_id is not None, "canvas should own an image item")
    check(app.canvas.itemcget(app._image_id, "image") != "", "image item must have a photo")
    check(app.layer_info_var.get().startswith("当前"), "status bar should show the floor")
    app.on_motion(FakeEvent(int(1.5 * size), int(1.5 * size)))
    check(app.coord_var.get().startswith("(1, 1)"), f"coord label: {app.coord_var.get()!r}")

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

    root.destroy()
    print("ok    GUI smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
