"""Self-contained checks for the maze data model, config and renderer.

Run with::

    python tests/test_maze.py

No third-party test runner is required; every check is a plain function that
raises ``AssertionError`` on failure.  Run it with pytest as well if you like.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    DEFAULT_FLOOR_COLOR_GROUP,
    DEFAULT_OBJECT_COLOR_GROUP,
    Config,
    hex_to_rgb,
)
from maze import (  # noqa: E402
    AIR,
    AXIS_X,
    AXIS_Y,
    GROUND,
    PORTAL,
    TEXT,
    CellData,
    Maze,
    build_sample_maze,
    edge_cells,
    edge_key,
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_object_color_group_cycles() -> None:
    config = Config({})
    assert config.object_color_group == DEFAULT_OBJECT_COLOR_GROUP
    # Above ground: 1 -> 黑, 2 -> 红, 8 -> 紫, 9 -> 黑 again.
    assert config.object_color_for_floor(1) == "#000000"
    assert config.object_color_for_floor(2) == "#FF0000"
    assert config.object_color_for_floor(8) == "#8000FF"
    assert config.object_color_for_floor(9) == "#000000"
    assert config.object_color_for_floor(16) == "#8000FF"
    assert config.object_color_for_floor(17) == "#000000"
    # The original names still work.
    assert config.color_group == config.object_color_group
    assert config.color_for_floor(2) == config.object_color_for_floor(2)


def test_floor_color_group_defaults_to_white_and_cycles() -> None:
    config = Config({})
    assert config.floor_color_group == ["#FFFFFF"]
    assert config.floor_color_group == DEFAULT_FLOOR_COLOR_GROUP
    # Every floor is plain white out of the box.
    assert [config.floor_color_for_floor(f) for f in range(1, 6)] == ["#FFFFFF"] * 5

    # A user supplied group cycles by floor number exactly like the object one.
    custom = Config({"floor_color_group": ["#FFFFFF", "#EEEEEE", "#DDDDDD"]})
    assert [custom.floor_color_for_floor(f) for f in range(1, 8)] == [
        "#FFFFFF", "#EEEEEE", "#DDDDDD",
        "#FFFFFF", "#EEEEEE", "#DDDDDD",
        "#FFFFFF",
    ]
    # A single colour may be given without the list wrapper.
    assert Config({"floor_color_group": "#123456"}).floor_color_for_floor(9) == "#123456"
    # A broken group falls back to the default instead of crashing.
    assert Config({"floor_color_group": ["nope"]}).floor_color_group == ["#FFFFFF"]
    assert Config({"floor_color_group": []}).floor_color_group == ["#FFFFFF"]


def test_legacy_color_group_key_is_still_read() -> None:
    """``color_group`` was renamed to ``object_color_group``."""
    legacy = Config({"color_group": ["#111111", "#222222"]})
    assert legacy.object_color_group == ["#111111", "#222222"]
    # The new key wins when both are present.
    both = Config({"color_group": ["#111111"], "object_color_group": ["#333333"]})
    assert both.object_color_group == ["#333333"]


def test_layer_colors_follow_floor_number() -> None:
    config = Config({})
    # Only above-ground floors: the bottom layer is 1 层 -> 黑 / 红 / 橙.
    maze = Maze(2, 2, layers=3)
    assert [maze.floor_of(i) for i in range(3)] == [1, 2, 3]
    assert [config.object_color_for_floor(maze.floor_of(i)) for i in range(3)] == \
        ["#000000", "#FF0000", "#FF7F00"]

    # Adding a layer above appends the next floor number.
    maze.add_layer(above=True)
    assert [maze.floor_of(i) for i in range(4)] == [1, 2, 3, 4]
    assert config.object_color_for_floor(maze.floor_of(3)) == "#FFFF00"   # 黄

    # The palette wraps around after 8 floors.
    for _ in range(5):
        maze.add_layer(above=True)
    assert maze.floor_of(8) == 9
    assert config.object_color_for_floor(maze.floor_of(8)) == "#000000"   # 9 层回到黑


def test_portal_color_default() -> None:
    assert Config({}).portal_color == "#FF00FF"
    assert Config({"portal_color": "#123456"}).portal_color == "#123456"
    # A broken colour falls back to the default instead of crashing.
    assert Config({"portal_color": "nope"}).portal_color == "#FF00FF"


def test_hex_to_rgb() -> None:
    assert hex_to_rgb("#FF00FF") == (255, 0, 255)
    assert hex_to_rgb("00ff00") == (0, 255, 0)
    assert hex_to_rgb("#abc") == (170, 187, 204)
    try:
        hex_to_rgb("zzzzzz")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("invalid colour must raise ValueError")


# ---------------------------------------------------------------------------
# edges / connectivity
# ---------------------------------------------------------------------------
def test_edge_key_is_undirected() -> None:
    assert edge_key((3, 4), (4, 4)) == (3, 4, AXIS_X)
    assert edge_key((4, 4), (3, 4)) == (3, 4, AXIS_X)
    assert edge_key((3, 4), (3, 5)) == (3, 4, AXIS_Y)
    assert edge_key((3, 5), (3, 4)) == (3, 4, AXIS_Y)
    assert edge_cells((3, 4, AXIS_X)) == ((3, 4), (4, 4))
    assert edge_cells((3, 4, AXIS_Y)) == ((3, 4), (3, 5))


def test_default_is_open() -> None:
    maze = Maze(4, 4, 1)
    layer = maze.layer(0)
    assert layer.passable((1, 1), (2, 1))
    assert layer.passable((2, 1), (1, 1))
    assert layer.edge_state((1, 1), (2, 1)) == (True, True)


def test_three_edge_states() -> None:
    maze = Maze(4, 4, 1)
    layer = maze.layer(0)
    a, b = (1, 1), (2, 1)

    layer.set_edge_state(a, b, "open")
    assert layer.edge_state(a, b) == (True, True)
    assert not layer.edges  # fully open edges are not stored

    layer.set_edge_state(a, b, "wall")
    assert layer.edge_state(a, b) == (False, False)
    assert not layer.passable(a, b) and not layer.passable(b, a)

    layer.set_edge_state(a, b, "oneway")
    assert layer.edge_state(a, b) == (True, False)
    assert layer.passable(a, b) and not layer.passable(b, a)

    layer.set_edge(a, b, False, True)  # the other direction
    assert layer.edge_state(a, b) == (False, True)
    assert not layer.passable(a, b) and layer.passable(b, a)


def test_edge_state_order_independent() -> None:
    maze = Maze(4, 4, 1)
    layer = maze.layer(0)
    layer.set_edge((2, 1), (1, 1), True, False)  # high -> low, reversed arguments
    assert layer.edge_state((1, 1), (2, 1)) == (False, True)
    assert layer.passable((2, 1), (1, 1))
    assert not layer.passable((1, 1), (2, 1))


def test_open_edges_and_reachability() -> None:
    maze = Maze(3, 1, 1)
    layer = maze.layer(0)
    layer.set_edge_state((0, 0), (1, 0), "oneway")   # 0 -> 1 only
    layer.set_edge_state((1, 0), (2, 0), "wall")     # 1 and 2 are cut off
    assert layer.open_edges(0, 0) == {"E": True}
    assert layer.open_edges(1, 0) == {"E": False, "W": False}
    assert layer.reachable_from((0, 0)) == {(0, 0), (1, 0)}
    assert layer.reachable_from((2, 0)) == {(2, 0)}

    layer.set_edge_state((1, 0), (2, 0), "open")
    assert layer.reachable_from((0, 0)) == {(0, 0), (1, 0), (2, 0)}


def test_wall_all_and_open_all() -> None:
    maze = Maze(3, 3, 1)
    layer = maze.layer(0)
    layer.wall_all()
    # 3x3 grid has 3*2 + 3*2 = 12 internal edges.
    assert len(layer.edges) == 12
    assert not layer.passable((0, 0), (1, 0))
    layer.open_all()
    assert not layer.edges
    assert layer.passable((0, 0), (1, 0))


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------
def test_cell_kinds_roundtrip() -> None:
    maze = Maze(4, 3, 1)
    layer = maze.layer(0)
    layer.set_cell(0, 0, CellData(kind=GROUND))
    layer.set_cell(1, 0, CellData(kind=TEXT, text="入口"))
    layer.set_cell(2, 0, CellData(kind=PORTAL, portal=(0, 2)))
    layer.set_cell(3, 0, CellData(kind=AIR))

    assert layer.get_cell(0, 0).kind == GROUND
    assert layer.get_cell(1, 0).text == "入口"
    assert layer.get_cell(2, 0).portal == (0, 2)
    assert layer.get_cell(3, 0).kind == AIR
    # Air cells are not stored at all (keeps files small).
    assert (3, 0) not in layer.cells
    # Out-of-bounds reads return a neutral cell instead of raising.
    assert layer.get_cell(-1, -1).kind == AIR


def test_cell_data_json() -> None:
    data = CellData(kind=TEXT, text="hello")
    assert CellData.from_json(data.to_json()) == data
    data = CellData(kind=PORTAL, portal=(4, 5))
    assert CellData.from_json(data.to_json()).portal == (4, 5)
    # Unknown kinds degrade to air rather than raising.
    assert CellData.from_json({"kind": "???", "text": "x"}).kind == AIR


# ---------------------------------------------------------------------------
# layers / stack
# ---------------------------------------------------------------------------
def test_add_remove_layer_shifts_floors() -> None:
    maze = Maze(2, 2, layers=2)
    maze.active = 1
    maze.add_layer(above=False)  # new bottom layer: the stack renumbers to 1, 2, 3
    assert maze.layer_count == 3
    assert maze.start_floor == 1
    assert [maze.floor_of(i) for i in range(3)] == [1, 2, 3]
    assert maze.active == 2
    maze.remove_layer(0)
    assert maze.start_floor == 1
    assert maze.active == 1
    assert maze.layer_count == 2
    # The last layer may never be removed.
    assert maze.remove_layer(0) is True
    assert maze.remove_layer(0) is False
    assert maze.layer_count == 1


def test_floor_numbering_is_stack_position() -> None:
    """Only above-ground floors exist: bottom layer is 1 层, and no 0 层."""
    maze = Maze(2, 2, layers=2)
    assert maze.floor_of(0) == 1 and maze.floor_of(1) == 2
    assert maze.index_of_floor(1) == 0 and maze.index_of_floor(2) == 1
    maze.add_layer(above=True)
    assert [maze.floor_of(i) for i in range(3)] == [1, 2, 3]
    maze.add_layer(above=False)
    assert [maze.floor_of(i) for i in range(4)] == [1, 2, 3, 4]
    maze.remove_layer(0)
    assert [maze.floor_of(i) for i in range(3)] == [1, 2, 3]
    # There is never a floor numbered 0.
    assert 0 not in [maze.floor_of(i) for i in range(maze.layer_count)]


def test_move_layer_keeps_active() -> None:
    maze = Maze(2, 2, layers=3)
    maze.layer(0).set_cell(0, 0, CellData(kind=TEXT, text="bottom"))
    maze.active = 0
    maze.move_layer(0, 1)
    assert maze.active == 1
    assert maze.layer(1).get_cell(0, 0).text == "bottom"


def test_resize_drops_outside_data() -> None:
    maze = Maze(4, 4, 1)
    layer = maze.layer(0)
    layer.set_cell(3, 3, CellData(kind=GROUND))
    layer.set_edge((2, 3), (3, 3), False, False)
    maze.resize(2, 2)
    assert layer.width == 2 and layer.height == 2
    assert not layer.cells
    assert not layer.edges
    assert maze.width == 2


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------
def test_maze_save_load_roundtrip() -> None:
    maze = build_sample_maze()
    maze.active = 2
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "maze.json")
        maze.save(path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        assert raw["format"] == "maze-map-designer"
        assert raw["width"] == maze.width
        assert "basement" not in raw, "there are no underground floors any more"
        assert "lowest_floor" not in raw

        reloaded = Maze.load(path)
    assert reloaded.width == maze.width
    assert reloaded.height == maze.height
    assert reloaded.layer_count == maze.layer_count
    assert reloaded.active == 2
    assert reloaded.to_json() == maze.to_json()


def test_load_rejects_garbage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        try:
            Maze.load(bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("broken JSON must raise ValueError")

        empty = os.path.join(tmp, "empty.json")
        with open(empty, "w", encoding="utf-8") as handle:
            handle.write('{"width": 4, "height": 4}')
        try:
            Maze.load(empty)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("missing layers must raise ValueError")


def test_sample_maze_exercises_everything() -> None:
    maze = build_sample_maze()
    kinds = {data.kind for layer in maze.layers for data in layer.cells.values()}
    assert {GROUND, TEXT, PORTAL} <= kinds
    states = {tuple(state) for layer in maze.layers for state in layer.edges.values()}
    assert (False, False) in states      # solid wall
    assert (True, False) in states       # one-way gate
    assert (False, True) in states
    for layer in maze.layers:
        for data in layer.cells.values():
            if data.kind == PORTAL:
                assert data.portal is not None
                assert layer.contains(*data.portal)


# ---------------------------------------------------------------------------
# renderer
# ---------------------------------------------------------------------------
def test_alpha_schedule() -> None:
    from renderer import alpha_for_distance, transparency_percent_for_distance

    assert alpha_for_distance(0) == 255
    assert alpha_for_distance(1) == 128
    assert alpha_for_distance(2) == 64
    assert alpha_for_distance(3) == 32
    assert alpha_for_distance(4) == 16
    assert alpha_for_distance(-1) == 128  # symmetric: layers above and below
    assert transparency_percent_for_distance(0) == 0.0
    assert transparency_percent_for_distance(1) == 50.0
    assert transparency_percent_for_distance(2) == 75.0
    assert transparency_percent_for_distance(3) == 87.5


def test_render_produces_image() -> None:
    from renderer import MazeRenderer

    maze = build_sample_maze()
    config = Config({"cell_size": 20})
    renderer = MazeRenderer(config)
    image = renderer.render(maze, maze.active)
    assert image.mode == "RGBA"
    assert image.size == (maze.width * 20, maze.height * 20)
    # Something must actually have been drawn.
    assert image.getbbox() is not None


def test_render_transparency_of_other_floors() -> None:
    """Air cells on non-active floors stay see-through."""
    from renderer import MazeRenderer

    maze = Maze(2, 1, layers=2)
    # Bottom floor: fully painted white ground.  Top floor: air everywhere.
    for cell in maze.layer(0).iter_cells():
        maze.layer(0).set_cell(cell[0], cell[1], CellData(kind=GROUND))
    config = Config({"cell_size": 16})
    renderer = MazeRenderer(config)

    # Active = bottom floor: the white ground is fully opaque.
    bottom = renderer.render(maze, 0)
    assert bottom.getpixel((8, 8)) == (255, 255, 255, 255)

    # Active = top floor: the bottom floor shows through at 50 % alpha.
    top = renderer.render(maze, 1)
    r, g, b, a = top.getpixel((8, 8))
    assert a < 255, "the lower floor must become translucent"
    assert r > 240 and g > 240 and b > 240


def test_text_autosize_shrinks_for_long_text() -> None:
    from renderer import MazeRenderer

    renderer = MazeRenderer(Config({}))
    short = renderer._font_for_text("A", 40, 40)
    long = renderer._font_for_text("A" * 24, 40, 40)
    assert long.size <= short.size
    assert long.size >= 5  # never below the configured minimum


def test_cell_at() -> None:
    from renderer import MazeRenderer

    renderer = MazeRenderer(Config({"cell_size": 20}))
    assert renderer.cell_at(0, 0) == (0, 0)
    assert renderer.cell_at(19.9, 19.9) == (0, 0)
    assert renderer.cell_at(20, 45) == (1, 2)


# ---------------------------------------------------------------------------
# pixel level checks: colours, walls, one-way arrows, portals, floor alpha
# ---------------------------------------------------------------------------
def _renderer(cell_size: int = 40):
    from renderer import MazeRenderer

    return MazeRenderer(Config({
        "cell_size": cell_size,
        "wall": {"width": 2, "dash": [6, 4], "arrow_length": 0.22, "arrow_width": 2},
    }))


def test_floor_colours_reach_the_canvas() -> None:
    """Each floor's ink uses its palette colour; a lone floor shows it exactly."""
    size = 40
    maze = Maze(1, 1, layers=3)
    expected = {0: (0, 0, 0), 1: (255, 0, 0), 2: (255, 127, 0)}  # 黑 / 红 / 橙
    renderer = _renderer(size)

    for index, ink in expected.items():
        # A one-layer... no: build a stack tall enough that the layer under
        # test really has the floor number `index + 1`.
        single = Maze(1, 1, layers=index + 1)
        single.active = index
        single.layer(index).set_cell(0, 0, CellData(kind=TEXT, text="F"))
        image = renderer.render(single, index)
        pixels = image.load()
        found = any(pixels[x, y][:3] == ink for x in range(size) for y in range(size))
        assert found, f"floor {index + 1} should be drawn in {ink}"

    # All three floors stacked: the opaque active floor plus translucent ones.
    stacked = Maze(1, 1, layers=3)
    for index, text in enumerate(("1F", "2F", "3F")):
        stacked.layer(index).set_cell(0, 0, CellData(kind=TEXT, text=text))
    top_active = renderer.render(stacked, 2)
    assert any(top_active.getpixel((x, y))[:3] == expected[2]
               for x in range(size) for y in range(size)), "the active floor keeps its own ink"


def test_lower_floor_text_shows_through_translucently() -> None:
    """A black-text floor below an active empty floor stays visible but faded."""
    size = 40
    maze = Maze(1, 1, layers=2)
    maze.layer(0).set_cell(0, 0, CellData(kind=TEXT, text="F"))
    image = _renderer(size).render(maze, 1)  # the top floor is active and empty
    pixels = image.load()

    alphas = {pixels[x, y][3] for x in range(size) for y in range(size)}
    assert 255 not in alphas, "nothing on the lower floor may be fully opaque"
    assert 0 not in alphas, "the lower floor must still be visible"
    # Every pixel of the lower floor is composited at 50 %: its opaque white
    # floor and its black ink must therefore both arrive with alpha 128.
    assert alphas == {128}, f"the lower floor should be uniformly translucent, got {alphas}"
    # The ink is present (dark pixels exist) but never at full strength.
    assert any(max(pixels[x, y][:3]) < 128 for x in range(size) for y in range(size)), \
        "the lower floor's ink must still be visible"
    assert any(pixels[x, y][:3] == (255, 255, 255) for x in range(size) for y in range(size)), \
        "its white floor also shows"


def test_floor_color_group_paints_the_tiles() -> None:
    """The floor palette colours the tiles; the object palette stays on the ink."""
    from renderer import MazeRenderer

    size = 20
    renderer = MazeRenderer(Config({
        "cell_size": size,
        "floor_color_group": ["#112233", "#445566"],
        "object_color_group": ["#FF0000"],
        "wall": {"width": 2, "dash": [6, 4], "arrow_length": 0.22, "arrow_width": 2},
    }))

    # Floor 1 is a ground tile plus a text label.
    maze = Maze(1, 1, layers=1)
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    image = renderer.render(maze, 0)
    assert image.getpixel((size // 2, size // 2))[:3] == (0x11, 0x22, 0x33), \
        "the tile must use floor_color_group[0]"
    assert image.getpixel((0, 0))[:3] == (0x11, 0x22, 0x33), "the whole tile is painted"

    # Floor 2 uses the second entry of the floor group.
    two = Maze(1, 1, layers=2)
    two.active = 1
    two.layer(1).set_cell(0, 0, CellData(kind=GROUND))
    image2 = renderer.render(two, 1)
    assert image2.getpixel((size // 2, size // 2))[:3] == (0x44, 0x55, 0x66), \
        "the floor palette must cycle with the floor number"

    # Text is still drawn in the object colour, on top of the floor colour.
    text = Maze(1, 1, layers=1)
    text.layer(0).set_cell(0, 0, CellData(kind=TEXT, text="T"))
    image3 = renderer.render(text, 0)
    pixels = image3.load()
    assert any(pixels[x, y][:3] == (255, 0, 0) for x in range(size) for y in range(size)), \
        "text must keep using object_color_group"
    assert image3.getpixel((0, 0))[:3] == (0x11, 0x22, 0x33), \
        "the text cell's background is still the floor colour"


def test_white_floor_by_default_keeps_old_look() -> None:
    """With the default single-white floor group nothing changes visually."""
    size = 20
    maze = Maze(1, 1, layers=2)
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    maze.layer(1).set_cell(0, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    assert image.getpixel((size // 2, size // 2))[:3] == (255, 255, 255)


def test_solid_wall_and_one_way_arrow() -> None:
    size = 40
    maze = Maze(2, 1, layers=1)
    layer = maze.layer(0)
    for cell in layer.iter_cells():
        layer.set_cell(cell[0], cell[1], CellData(kind=GROUND))

    # A solid wall between the two cells, in the floor colour (黑).
    layer.set_edge_state((0, 0), (1, 0), "wall")
    image = _renderer(size).render(maze, 0)
    boundary_x = size
    column = [image.getpixel((boundary_x, y))[:3] for y in range(4, size - 4)]
    assert all(px == (0, 0, 0) for px in column), "a blocked edge must be a solid black line"

    # A one-way gate: dashed, so the boundary has gaps, plus an arrow head.
    layer.set_edge_state((0, 0), (1, 0), "oneway")
    image = _renderer(size).render(maze, 0)
    column = [image.getpixel((boundary_x, y))[:3] for y in range(2, size - 2)]
    assert any(px == (0, 0, 0) for px in column), "the dashed line must still draw ink"
    assert any(px != (0, 0, 0) for px in column), "a dashed line must have gaps"

    # The arrow head is centred on the edge and points towards (1, 0): the ink
    # reaches into the destination cell and stays inside the origin cell.
    mid_y = size // 2
    ink = [x for x in range(size - 12, size + 12)
           if image.getpixel((x, mid_y))[:3] == (0, 0, 0)]
    assert ink, "the one-way gate must be drawn"
    assert min(ink) >= size - 12, "the head must not spill out of the origin cell"
    assert max(ink) <= size + 1, "the head must not spill into the destination cell"


def _arrow_ink_profile(forward: bool, size: int = 48):
    """Per-column ink counts around the shared edge of (0, 0) and (1, 0).

    Returns a list of ``(x, pixel_count)`` for the columns of a band around the
    boundary, counting ink over all rows of the wall.
    """
    maze = Maze(2, 1, layers=1)
    layer = maze.layer(0)
    for cell in layer.iter_cells():
        layer.set_cell(cell[0], cell[1], CellData(kind=GROUND))
    layer.set_edge((0, 0), (1, 0), forward, not forward)
    image = _renderer(size).render(maze, 0)
    return [(x, sum(1 for y in range(size)
                    if image.getpixel((x, y))[:3] == (0, 0, 0)))
            for x in range(size - 14, size + 14)]


def test_one_way_arrow_head_is_centred_on_the_edge() -> None:
    """The head is a triangle whose apex touches the edge and whose base lies
    inside the cell the traveller starts from.

    ``size`` is 48 on purpose: the dash pattern ``[6, 4]`` then tiles the wall
    exactly, so the boundary columns always carry dash ink.
    """
    size = 48
    boundary = size

    profile = dict(_arrow_ink_profile(True, size))  # 0,0 -> 1,0, head points right
    widths = {x: n for x, n in profile.items() if n > 0}
    assert widths, "the gate must be drawn"

    # The dashed boundary itself covers exactly the two boundary columns and
    # is much taller than the head (the head is at most ~half a cell wide).
    tall = {x for x, n in widths.items() if n >= size // 2}
    assert tall == {boundary, boundary + 1}, f"the dashed line must span the boundary: {widths}"

    head = {x: n for x, n in widths.items() if x not in tall}
    assert head, "an arrow head must be drawn next to the dashed line"
    assert max(head) == boundary - 1, f"the apex must touch the edge: {head}"
    assert min(head) == boundary - 11, f"the head must have a length: {head}"
    # The apex is the narrow end and the base the wide one.
    assert head[boundary - 1] == 1, "the apex must be one pixel tall"
    assert head[boundary - 11] >= 5, "the base must be wide"
    assert head[boundary - 11] > head[boundary - 1], "the head must widen away from the edge"
    # The whole head stays inside the origin cell (0, 0).
    assert min(head) >= 0, "the head must not leave the maze"


def test_one_way_arrow_direction_is_mirrored() -> None:
    """Flipping the allowed direction mirrors the head, it does not move it."""
    size = 48
    boundary = size

    def head_profile(forward: bool) -> list:
        """Ink per column of the head, read outwards from the dashed boundary.

        The two boundary columns are covered by the dashed line, so they are
        skipped; what remains is the triangle, ordered from apex to base.
        """
        widths = dict(_arrow_ink_profile(forward, size))
        columns = range(boundary - 1, boundary - 13, -1) if forward \
            else range(boundary + 2, boundary + 14)
        profile = [widths.get(x, 0) for x in columns]
        while profile and profile[-1] == 0:   # trim the over-wide window
            profile.pop()
        return profile

    right = head_profile(True)    # 0,0 -> 1,0 : head grows to the left of the edge
    left = head_profile(False)    # 1,0 -> 0,0 : head grows to the right of the edge

    # Both are the same triangle: apex on the edge, widening towards the base.
    for name, profile in (("right", right), ("left", left)):
        assert profile[0] == 1, f"the {name} apex must touch the edge: {profile}"
        assert profile[-1] >= 4, f"the {name} base must be wide: {profile}"
        assert all(profile[i] <= profile[i + 1] for i in range(len(profile) - 1)), \
            f"the {name} head must widen monotonically: {profile}"
    # The two heads are the same size, mirrored around the edge.  Pillow's
    # polygon rasteriser rounds the mirrored triangle slightly differently, so
    # allow a couple of pixels of slack.
    assert abs(len(right) - len(left)) <= 2, f"head lengths differ: {right} vs {left}"
    assert abs(right[-1] - left[-1]) <= 2, f"head widths differ: {right} vs {left}"
    # And they really are on opposite sides of the shared edge.
    widths_forward = dict(_arrow_ink_profile(True, size))
    widths_backward = dict(_arrow_ink_profile(False, size))
    assert widths_forward.get(boundary - 5, 0) > 0, "the head must lie in (0, 0)"
    assert widths_backward.get(boundary + 6, 0) > 0, "the head must lie in (1, 0)"


def test_portal_arrow_uses_its_own_colour() -> None:
    from config import hex_to_rgb

    size = 40
    maze = Maze(4, 1, layers=1)
    layer = maze.layer(0)
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=(3, 0)))
    layer.set_cell(3, 0, CellData(kind=GROUND))
    renderer = _renderer(size)
    image = renderer.render(maze, 0)

    portal_rgb = hex_to_rgb(renderer.config.portal_color)
    assert portal_rgb == (255, 0, 255)
    # The shaft runs along the vertical centre of the row.
    mid_y = size // 2
    shaft = [image.getpixel((x, mid_y))[:3] for x in range(size, 3 * size)]
    assert any(px == portal_rgb for px in shaft), "the portal arrow must be #FF00FF"
    # The arrow head is at the destination centre, i.e. x = 3.5 * size.
    tip_x = int(3.5 * size)
    near_tip = [image.getpixel((x, y))[:3]
                for x in range(tip_x - 6, tip_x - 1) for y in range(mid_y - 3, mid_y + 4)]
    assert any(px == portal_rgb for px in near_tip), "the arrow must reach the destination centre"


def test_portal_without_target_draws_a_ring() -> None:
    from config import hex_to_rgb

    size = 40
    maze = Maze(2, 2, layers=1)
    maze.layer(0).set_cell(0, 0, CellData(kind=PORTAL, portal=None))
    renderer = _renderer(size)
    image = renderer.render(maze, 0)
    portal_rgb = hex_to_rgb(renderer.config.portal_color)
    ring = [image.getpixel((x, y))[:3] for x in range(size) for y in range(size)]
    assert portal_rgb in ring, "an unset portal should still be visible"


def test_lower_floor_is_painted_over_by_higher_floor() -> None:
    """Drawing order is bottom-to-top, so the upper floor wins where both paint."""
    size = 20
    maze = Maze(1, 1, layers=2)
    maze.layer(0).set_cell(0, 0, CellData(kind=TEXT, text="A"))   # 黑 ink, white fill
    maze.layer(1).set_cell(0, 0, CellData(kind=GROUND))           # opaque white fill
    renderer = _renderer(size)
    image = renderer.render(maze, 1)  # top floor is active
    # The top floor is opaque white, so the lower floor's text is covered.
    assert image.getpixel((size // 2, 2))[:3] == (255, 255, 255)
    assert image.getpixel((size // 2, 2))[3] == 255


def test_empty_maze_is_fully_transparent() -> None:
    """Air cells are transparent, so an empty maze composites to nothing."""
    size = 20
    maze = Maze(2, 1, layers=1)
    image = _renderer(size).render(maze, 0)
    assert image.getbbox() is None, "an empty maze must stay fully transparent"


def test_renderer_draws_no_red_frame() -> None:
    """The composite is pure maze content: no red outline around the grid."""
    size = 20
    maze = Maze(3, 2, layers=1)
    for cell in maze.layer(0).iter_cells():
        maze.layer(0).set_cell(cell[0], cell[1], CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    width, height = image.size
    border = ([image.getpixel((x, 0))[:3] for x in range(width)]
              + [image.getpixel((x, height - 1))[:3] for x in range(width)]
              + [image.getpixel((0, y))[:3] for y in range(height)]
              + [image.getpixel((width - 1, y))[:3] for y in range(height)])
    assert all(px == (255, 255, 255) for px in border), \
        f"the outer border must stay plain floor, got {sorted(set(border))}"


def test_portal_arrow_is_not_covered_by_neighbour_floors() -> None:
    """Arrows are drawn after all floors and walls of the same layer."""
    from config import hex_to_rgb

    size = 40
    maze = Maze(3, 1, layers=1)
    layer = maze.layer(0)
    # The portal cell is stored first, so a naive single-pass loop would paint
    # the white floor of (2, 0) over the arrow head at the destination centre.
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=(2, 0)))
    layer.set_cell(2, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    portal_rgb = hex_to_rgb(_renderer(size).config.portal_color)
    # Sample a band just left of the destination centre (2.5 * size).
    tip_x = int(2.5 * size)
    band = [image.getpixel((x, y))[:3]
            for x in range(tip_x - 8, tip_x) for y in range(size // 2 - 4, size // 2 + 5)]
    assert portal_rgb in band, "the arrow head must remain visible on top of the floor"


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, func in tests:
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
        else:
            print(f"ok    {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
