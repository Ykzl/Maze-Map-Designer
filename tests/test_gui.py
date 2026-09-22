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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import MazeEditorApp, BRUSH_CELL, BRUSH_WALL, BRUSH_PORTAL  # noqa: E402
from config import Config  # noqa: E402
from maze import AIR, CELL_KIND_LABELS, GROUND, PORTAL, TEXT, Maze  # noqa: E402


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

    # --- paint a ground cell with the left button ----------------------
    app.brush_var.set(BRUSH_CELL)
    app.cell_kind_var.set(CELL_KIND_LABELS[GROUND])
    app.on_left_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.get_cell(2, 1).kind == GROUND, "left click should paint a ground cell")
    check(app.dirty, "painting must mark the document dirty")

    # --- erase it with the right button -------------------------------
    app.on_right_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.get_cell(2, 1).kind == AIR, "right click should erase the cell")

    # --- wall brush: the shared edge of (2,1) and (3,1) ---------------
    app.brush_var.set(BRUSH_WALL)
    app.wall_state_var.set("不连通（实线）")
    edge_x = 3 * size + 1  # just inside the boundary between x=2 and x=3
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.passable((2, 1), (3, 1)) is False, "wall brush should block the edge")
    check(maze.passable((3, 1), (2, 1)) is False, "wall brush should block both ways")

    # A click in the middle of a cell is too far from any edge to count.
    app.on_left_click(FakeEvent(int(2.5 * size), int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (False, False),
          "a centre click must not touch walls")

    # One-way gate, then the same edge again via the right button cycle.
    app.wall_state_var.set("单向连通（虚线+箭头）")
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (True, False),
          "one-way gate should pass 2,1 -> 3,1")
    app.on_left_click(FakeEvent(edge_x, int(1.5 * size), state=0x0001))  # Shift
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (False, True),
          "shift+click should flip the gate direction")
    app.on_right_click(FakeEvent(edge_x, int(1.5 * size)))
    check(maze.active_layer().edge_state((2, 1), (3, 1)) == (True, True),
          "right click should cycle back to open")

    # --- portal brush: two clicks --------------------------------------
    app.brush_var.set(BRUSH_PORTAL)
    app.on_left_click(FakeEvent(int(1.5 * size), int(3.5 * size)))
    check(app.pending_portal_source == (1, 3), "first portal click sets the source")
    app.on_left_click(FakeEvent(int(4.5 * size), int(3.5 * size)))
    portal = maze.get_cell(1, 3)
    check(portal.kind == PORTAL and portal.portal == (4, 3),
          f"second portal click sets the destination, got {portal}")
    check(app.pending_portal_source is None, "portal pending state must be cleared")

    # --- text cell through the dialog-free path ------------------------
    maze.set_cell(5, 4, __import__("maze").CellData(kind=TEXT, text="测试文本"))
    app.refresh()

    # --- floors ---------------------------------------------------------
    check(app.renderer.render(maze, 0).size == (6 * size, 5 * size), "render size")
    app.goto_layer(2)
    check(maze.active == 2, "goto_layer should switch the active layer")
    app.step_layer(-1)
    check(maze.active == 1, "step_layer(-1) should move down one floor")
    app.add_layer(True)
    check(maze.layer_count == 4, "add_layer should grow the stack")
    app.remove_layer_confirmed = True

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
