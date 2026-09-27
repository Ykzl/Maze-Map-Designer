"""Render the three edge states side by side at area ratio 50 %.

Four ground cells in a row: a walled edge, a one-way edge and an open edge.
Used for ``preview_f_states_area50.png`` in the README.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from maze import GROUND, CellData, Maze  # noqa: E402
from renderer import MazeRenderer  # noqa: E402

SIZE = 80


def main() -> None:
    maze = Maze(4, 1, layers=1)
    maze.active = 0
    layer = maze.layer(0)
    layer.area_ratio = 50
    for x in range(4):
        layer.set_cell(x, 0, CellData(kind=GROUND))
    layer.set_edge_state((0, 0), (1, 0), "wall")     # two parallel walls, gap between
    layer.set_edge((1, 0), (2, 0), True, False)      # one-way: arms meet, gate on the border
    # (2, 0) - (3, 0) stays open: nothing is drawn there.

    renderer = MazeRenderer(Config({"cell_size": SIZE,
                                    "wall": {"dash": [6, 4], "arrow_length": 0.22,
                                             "arrow_width": 2}}))
    image = renderer.render_on_checkerboard(maze, 0)
    image.convert("RGB").save("preview_f_states_area50.png")
    print("wrote preview_f_states_area50.png", image.size, image.getbbox())


if __name__ == "__main__":
    main()
