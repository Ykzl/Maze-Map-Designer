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

from config import Config, hex_to_rgb  # noqa: E402
from maze import (  # noqa: E402
    AIR,
    AXIS_X,
    AXIS_Y,
    DEFAULT_AREA_RATIO,
    DEFAULT_FLOOR_COLOR,
    DEFAULT_OBJECT_COLOR,
    DEFAULT_PORTAL_COLOR,
    DEFAULT_WALL_WIDTH,
    GROUND,
    PORTAL,
    TEXT,
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


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_config_no_longer_holds_per_floor_appearance() -> None:
    """The four per-floor settings moved out of config.json into the maze file."""
    config = Config({})
    for gone in ("object_color_group", "floor_color_group", "portal_color"):
        assert not hasattr(config, gone), f"Config must not expose {gone} any more"
    assert "width" not in config.wall, "wall width is stored per layer now"
    # The global wall settings that stayed behind.
    assert config.wall["dash"] == [6, 4]
    assert config.wall["arrow_length"] == 0.22
    assert config.wall["arrow_width"] == 3


def test_old_config_keys_are_ignored() -> None:
    """An old config.json still loads; its moved keys simply have no effect."""
    legacy = Config({
        "object_color_group": ["#FF0000"],
        "floor_color_group": ["#00FF00"],
        "portal_color": "#123456",
        "color_group": ["#0000FF"],
        "wall": {"width": 9, "dash": [2, 2]},
        "cell_size": 33,
    })
    assert not hasattr(legacy, "object_color_group")
    assert "width" not in legacy.wall
    assert legacy.wall["dash"] == [2, 2]      # untouched keys still apply
    assert legacy.cell_size == 33


def test_layer_appearance_defaults() -> None:
    """New layers -- and therefore new mazes -- use the documented defaults."""
    layer = Layer(3, 2)
    assert layer.object_color == DEFAULT_OBJECT_COLOR == "#000000"
    assert layer.floor_color == DEFAULT_FLOOR_COLOR == "#FFFFFF"
    assert layer.portal_color == DEFAULT_PORTAL_COLOR == "#FF00FF"
    assert layer.wall_width == DEFAULT_WALL_WIDTH == 3
    assert layer.area_ratio == DEFAULT_AREA_RATIO == 100

    maze = Maze(3, 2, layers=2)
    for index in range(maze.layer_count):
        assert maze.layer(index).object_color == "#000000"
        assert maze.layer(index).floor_color == "#FFFFFF"

    # A layer added above or below starts with the defaults as well.
    maze = Maze(3, 2, layers=2)
    maze.layer(0).object_color = "#123456"
    maze.add_layer(above=True)          # appended on top
    assert maze.layer(2).object_color == "#000000"
    maze.add_layer(above=False)         # inserted at the bottom, everything shifts
    assert maze.layer(0).object_color == "#000000", "the new bottom layer is a default layer"
    assert maze.layer(1).object_color == "#123456", "the old layer kept its colour"


def test_color_and_wall_width_normalisation() -> None:
    assert normalise_color("#abc", "#000000") == "#AABBCC"
    assert normalise_color("00ff00", "#000000") == "#00FF00"
    assert normalise_color("  #FF00FF ", "#000000") == "#FF00FF"
    assert normalise_color("nope", "#123456") == "#123456"
    assert normalise_color("", "#123456") == "#123456"
    assert normalise_color(None, "#123456") == "#123456"
    assert normalise_color("#12345", "#123456") == "#123456"

    assert normalise_wall_width(5) == 5
    assert normalise_wall_width("7") == 7
    assert normalise_wall_width(0) == 1          # clamped
    assert normalise_wall_width(999) == 64       # clamped
    assert normalise_wall_width("x", 4) == 4     # fallback
    assert normalise_wall_width(None) == DEFAULT_WALL_WIDTH


def test_area_ratio_normalisation() -> None:
    """面积占比 is an integer in 0..100 and never raises on a broken value."""
    assert normalise_area_ratio(50) == 50
    assert normalise_area_ratio("33") == 33
    assert normalise_area_ratio(33.6) == 34
    assert normalise_area_ratio(-5) == 0         # clamped
    assert normalise_area_ratio(999) == 100      # clamped
    assert normalise_area_ratio("", 42) == 42    # fallback
    assert normalise_area_ratio("nope", 42) == 42
    assert normalise_area_ratio(None) == DEFAULT_AREA_RATIO

    assert Layer(1, 1).area_ratio == DEFAULT_AREA_RATIO == 100
    assert Layer(1, 1, area_ratio=35).area_ratio == 35
    assert Layer(1, 1, area_ratio=-5).area_ratio == 0
    assert Layer(1, 1, area_ratio="nope").area_ratio == 100


def test_cell_bands_geometry() -> None:
    """The four points of the specification: size 100, ratio 50 -> 25..75."""
    from renderer import cell_bands

    assert cell_bands(100, 100) == (0.0, 100)
    assert cell_bands(50, 100) == (25.0, 50), "the central block spans 25..75"
    assert cell_bands(25, 100) == (37.5, 25)
    assert cell_bands(0, 100) == (49.5, 1), "ratio 0 keeps a one pixel dot"
    assert cell_bands(120, 100) == (0.0, 100), "an out of range ratio is clamped"
    assert cell_bands(50, 40) == (10.0, 20)


def test_portal_color_default() -> None:
    # The portal colour now lives on the layer, so a fresh layer carries it.
    assert Layer(1, 1).portal_color == "#FF00FF"
    layer = Layer(1, 1, portal_color="#123456")
    assert layer.portal_color == "#123456"
    # A broken colour falls back to the default instead of crashing.
    assert Layer(1, 1, portal_color="nope").portal_color == "#FF00FF"


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
    # Give every floor a distinct look: all five values must survive the trip.
    for index in range(maze.layer_count):
        layer = maze.layer(index)
        layer.object_color = ["#112233", "#445566", "#778899"][index]
        layer.floor_color = ["#FFFFFF", "#EEEEEE", "#DDDDDD"][index]
        layer.portal_color = ["#FF00FF", "#00FF00", "#0000FF"][index]
        layer.wall_width = index + 1
        layer.area_ratio = [100, 60, 0][index]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "maze.json")
        maze.save(path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        assert raw["format"] == "maze-map-designer"
        assert raw["width"] == maze.width
        assert "basement" not in raw, "there are no underground floors any more"
        assert "lowest_floor" not in raw
        # The appearance is stored per layer, inside the maze file.
        first = raw["layers"][0]
        assert first["object_color"] == "#112233"
        assert first["floor_color"] == "#FFFFFF"
        assert first["portal_color"] == "#FF00FF"
        assert first["wall_width"] == 1
        assert first["area_ratio"] == 100
        assert [item["area_ratio"] for item in raw["layers"]] == [100, 60, 0]

        reloaded = Maze.load(path)
    assert reloaded.width == maze.width
    assert reloaded.height == maze.height
    assert reloaded.layer_count == maze.layer_count
    assert reloaded.active == 2
    for index in range(reloaded.layer_count):
        original = maze.layer(index)
        loaded = reloaded.layer(index)
        assert loaded.object_color == original.object_color
        assert loaded.floor_color == original.floor_color
        assert loaded.portal_color == original.portal_color
        assert loaded.wall_width == original.wall_width
        assert loaded.area_ratio == original.area_ratio
    assert reloaded.to_json() == maze.to_json()


def test_load_old_file_without_appearance_uses_defaults() -> None:
    """A file written before per-layer appearance gets the documented defaults."""
    legacy = {
        "format": "maze-map-designer",
        "version": 1,
        "width": 2,
        "height": 2,
        "active": 1,
        "layers": [
            {"name": "", "cells": [], "edges": [[0, 0, 0, 0, 0]]},
            {"name": "", "cells": [], "edges": []},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "legacy.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        maze = Maze.load(path)
    assert maze.layer_count == 2
    assert maze.active == 1
    for layer in maze.layers:
        assert layer.object_color == DEFAULT_OBJECT_COLOR == "#000000"
        assert layer.floor_color == DEFAULT_FLOOR_COLOR == "#FFFFFF"
        assert layer.portal_color == DEFAULT_PORTAL_COLOR == "#FF00FF"
        assert layer.wall_width == DEFAULT_WALL_WIDTH == 3
        assert layer.area_ratio == DEFAULT_AREA_RATIO == 100
    # A broken colour in the file also falls back instead of aborting the load.
    legacy["layers"][0]["object_color"] = "not-a-colour"
    legacy["layers"][0]["wall_width"] = "huge"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        maze = Maze.load(path)
    assert maze.layer(0).object_color == "#000000"
    assert maze.layer(0).wall_width == 3
    assert maze.layer(0).area_ratio == 100


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
#: Wall width used by the pixel tests.  Wall width is a per-layer value now, so
#: tests that care about geometry set it on the layer they render.
TEST_WALL_WIDTH = 2


def _renderer(cell_size: int = 40):
    from renderer import MazeRenderer

    return MazeRenderer(Config({
        "cell_size": cell_size,
        "wall": {"dash": [6, 4], "arrow_length": 0.22, "arrow_width": 2},
    }))


def _styled_layer(maze: Maze, index: int = 0, wall_width: int = TEST_WALL_WIDTH):
    """Give a layer the width the pixel tests expect, and return it."""
    layer = maze.layer(index)
    layer.wall_width = wall_width
    return layer


def _ground(layer: Layer, kind: str = GROUND) -> None:
    """Fill a layer with cells that have a floor, i.e. with cells that draw."""
    for cell in layer.iter_cells():
        layer.set_cell(cell[0], cell[1], CellData(kind=kind))


def test_layer_object_colours_reach_the_canvas() -> None:
    """Each layer is drawn in its own object colour, whatever the floor number."""
    size = 40
    renderer = _renderer(size)
    expected = {0: (0, 0, 0), 1: (255, 0, 0), 2: (255, 127, 0)}  # 黑 / 红 / 橙

    maze = Maze(1, 1, layers=3)
    for index, colour in zip(expected, ("#000000", "#FF0000", "#FF7F00")):
        maze.layer(index).object_color = colour
        maze.layer(index).set_cell(0, 0, CellData(kind=TEXT, text="F"))

    # Each floor on its own, so every pixel belongs to that floor.
    for index, ink in expected.items():
        image = renderer.render_layer(maze, index)
        pixels = image.load()
        found = sum(1 for x in range(size) for y in range(size)
                    if pixels[x, y][3] > 0 and pixels[x, y][:3] == ink)
        assert found > 0, f"floor {index + 1} should be drawn in {ink}"

    # The colour lives on the *layer*, not on the floor number: swapping two
    # layers' colours swaps their ink as well.
    maze.layer(0).object_color = "#FF7F00"
    maze.layer(2).object_color = "#000000"
    swapped = renderer.render_layer(maze, 0)
    pixels = swapped.load()
    assert any(pixels[x, y][:3] == (255, 127, 0) and pixels[x, y][3] > 0
               for x in range(size) for y in range(size)), "the layer keeps its own ink"
    assert not any(pixels[x, y][:3] == (0, 0, 0) and pixels[x, y][3] > 0
                   for x in range(size) for y in range(size)), "the old colour is gone"


def test_lower_floor_text_shows_through_translucently() -> None:
    """A black-text floor below an active empty floor stays visible but faded."""
    size = 40
    maze = Maze(1, 1, layers=2)
    maze.layer(0).object_color = "#000000"
    maze.layer(0).floor_color = "#FFFFFF"
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


def test_floor_color_paints_the_tiles() -> None:
    """The layer's floor colour fills the tiles; text keeps the object colour."""
    from renderer import MazeRenderer

    size = 20

    # Floor 1: a tile painted in the layer's floor colour.
    maze = Maze(1, 1, layers=1)
    maze.layer(0).floor_color = "#112233"
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    assert image.getpixel((size // 2, size // 2))[:3] == (0x11, 0x22, 0x33), \
        "the tile must use the layer's floor_color"
    assert image.getpixel((0, 0))[:3] == (0x11, 0x22, 0x33), "the whole tile is painted"

    # Floor 2 keeps its own, different floor colour.
    two = Maze(1, 1, layers=2)
    two.layer(0).floor_color = "#112233"
    two.layer(1).floor_color = "#445566"
    two.active = 1
    two.layer(1).set_cell(0, 0, CellData(kind=GROUND))
    image2 = _renderer(size).render(two, 1)
    assert image2.getpixel((size // 2, size // 2))[:3] == (0x44, 0x55, 0x66), \
        "each layer carries its own floor colour"

    # Text is drawn in the object colour, on top of the floor colour.
    text = Maze(1, 1, layers=1)
    text.layer(0).floor_color = "#112233"
    text.layer(0).object_color = "#FF0000"
    text.layer(0).set_cell(0, 0, CellData(kind=TEXT, text="T"))
    image3 = _renderer(size).render(text, 0)
    pixels = image3.load()
    assert any(pixels[x, y][:3] == (255, 0, 0) for x in range(size) for y in range(size)), \
        "text must use the layer's object_color"
    assert image3.getpixel((0, 0))[:3] == (0x11, 0x22, 0x33), \
        "the text cell's background is still the floor colour"


def test_wall_width_comes_from_the_layer() -> None:
    """A thicker wall width paints more ink, and it is read from the layer."""
    from renderer import MazeRenderer

    size = 40

    def ink(width: int) -> int:
        """Pixels painted in the object colour: a wider wall paints more of them.

        The alpha sum cannot be used any more: the ground cells themselves fill
        the image with opaque floor colour, so only the black ink of the wall
        tells the two widths apart.
        """
        maze = Maze(2, 1, layers=1)
        layer = _styled_layer(maze, wall_width=width)
        _ground(layer)
        layer.set_edge((0, 0), (1, 0), False, False)
        image = _renderer(size).render(maze, 0)
        return sum(1 for x in range(image.width) for y in range(image.height)
                   if image.getpixel((x, y))[3] > 0
                   and image.getpixel((x, y))[:3] == (0, 0, 0))

    thin, thick = ink(1), ink(6)
    assert thin > 0, "the wall must be drawn"
    assert thick > thin * 3, f"a wider wall must paint more ink ({thin} vs {thick})"

    # The configured global width is ignored: only the layer's value counts.
    renderer = MazeRenderer(Config({"cell_size": size,
                                    "wall": {"width": 1, "dash": [6, 4]}}))
    maze = Maze(2, 1, layers=1)
    layer = _styled_layer(maze, wall_width=8)
    _ground(layer)
    layer.set_edge((0, 0), (1, 0), False, False)
    wide = renderer.render(maze, 0)
    assert sum(1 for x in range(wide.width) for y in range(wide.height)
               if wide.getpixel((x, y))[3] > 0
               and wide.getpixel((x, y))[:3] == (0, 0, 0)) > thin, \
        "the layer's wall width must win over the global one"


def test_white_floor_by_default_keeps_old_look() -> None:
    """The default black-on-white look is unchanged."""
    size = 20
    maze = Maze(1, 1, layers=2)
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    maze.layer(1).set_cell(0, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    assert image.getpixel((size // 2, size // 2))[:3] == (255, 255, 255)


def test_solid_wall_and_one_way_arrow() -> None:
    size = 40
    maze = Maze(2, 1, layers=1)
    layer = _styled_layer(maze)
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
    layer = _styled_layer(maze)
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
    layer = _styled_layer(maze)
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=(3, 0)))
    layer.set_cell(3, 0, CellData(kind=GROUND))
    renderer = _renderer(size)
    image = renderer.render(maze, 0)

    portal_rgb = hex_to_rgb(layer.portal_color)
    assert portal_rgb == (255, 0, 255), "the default portal colour is #FF00FF"
    # The shaft runs along the vertical centre of the row.
    mid_y = size // 2
    shaft = [image.getpixel((x, mid_y))[:3] for x in range(size, 3 * size)]
    assert any(px == portal_rgb for px in shaft), "the portal arrow must be #FF00FF"
    # The arrow head is at the destination centre, i.e. x = 3.5 * size.
    tip_x = int(3.5 * size)
    near_tip = [image.getpixel((x, y))[:3]
                for x in range(tip_x - 6, tip_x - 1) for y in range(mid_y - 3, mid_y + 4)]
    assert any(px == portal_rgb for px in near_tip), "the arrow must reach the destination centre"


def test_portal_colour_is_per_layer() -> None:
    """The portal colour comes from the layer, not from a global setting."""
    size = 32
    maze = Maze(4, 1, layers=1)
    layer = _styled_layer(maze)
    layer.portal_color = "#00FF00"
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=(3, 0)))
    layer.set_cell(3, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    mid_y = size // 2
    shaft = [image.getpixel((x, mid_y))[:3] for x in range(size, 3 * size)]
    assert (0, 255, 0) in shaft, "the layer's portal colour must be used"
    assert (255, 0, 255) not in shaft, "the old global colour must not appear"


def test_portal_without_target_draws_a_ring() -> None:
    size = 40
    maze = Maze(2, 2, layers=1)
    layer = _styled_layer(maze)
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=None))
    image = _renderer(size).render(maze, 0)
    portal_rgb = hex_to_rgb(layer.portal_color)
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
    layer = _styled_layer(maze)
    # The portal cell is stored first, so a naive single-pass loop would paint
    # the white floor of (2, 0) over the arrow head at the destination centre.
    layer.set_cell(0, 0, CellData(kind=PORTAL, portal=(2, 0)))
    layer.set_cell(2, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    portal_rgb = hex_to_rgb(layer.portal_color)
    # Sample a band just left of the destination centre (2.5 * size).
    tip_x = int(2.5 * size)
    band = [image.getpixel((x, y))[:3]
            for x in range(tip_x - 8, tip_x) for y in range(size // 2 - 4, size // 2 + 5)]
    assert portal_rgb in band, "the arrow head must remain visible on top of the floor"


# ---------------------------------------------------------------------------
# pixel level checks for 面积占比 (area ratio)
# ---------------------------------------------------------------------------
#: size 40 at 50 % -> inset 10, central block 20x20 (the specification's
#: "size 100, ratio 50 -> 25..75" divided by 2.5).
AREA_SIZE = 40
AREA_INSET = 10
AREA_INNER = 20


def test_area_ratio_100_keeps_the_full_cell_look() -> None:
    """area_ratio 100 reproduces the old geometry: full tile, wall on the edge."""
    size = AREA_SIZE
    maze = Maze(2, 1, layers=1)
    layer = _styled_layer(maze)
    _ground(layer)
    layer.set_edge_state((0, 0), (1, 0), "wall")
    image = _renderer(size).render(maze, 0)
    assert image.getpixel((0, 0))[:3] == (255, 255, 255), "the whole cell is floor"
    assert image.getpixel((size - 1, size - 1))[:3] == (255, 255, 255)
    column = [image.getpixel((size, y))[:3] for y in range(4, size - 4)]
    assert all(px == (0, 0, 0) for px in column), "the wall sits on the shared edge"


def test_air_cells_draw_no_walls_at_all() -> None:
    """A cell whose floor is air paints neither floor nor walls, ever."""
    size = 20
    maze = Maze(2, 1, layers=1)
    _styled_layer(maze)
    maze.layer(0).set_edge_state((0, 0), (1, 0), "wall")
    image = _renderer(size).render(maze, 0)
    assert image.getbbox() is None, "an edge between two air cells stays invisible"

    # With one ground cell the wall comes back, drawn by that cell alone.
    maze.layer(0).set_cell(0, 0, CellData(kind=GROUND))
    image = _renderer(size).render(maze, 0)
    assert image.getpixel((size, size // 2))[:3] == (0, 0, 0), \
        "the ground cell still walls itself off from the air neighbour"
    assert image.getpixel((size + size // 2, size // 2))[3] == 0, \
        "the air cell itself stays empty"


def test_area_ratio_shrinks_the_floor_towards_the_cell_centre() -> None:
    """One ground cell among air: only its central block is painted."""
    maze = Maze(3, 3, layers=1)
    layer = _styled_layer(maze)
    layer.set_cell(1, 1, CellData(kind=GROUND))
    layer.area_ratio = 50
    image = _renderer(AREA_SIZE).render(maze, 0)
    x0, y0 = AREA_SIZE, AREA_SIZE
    half = AREA_INNER // 2
    assert image.getpixel((x0 + AREA_INSET + half, y0 + AREA_INSET + half))[:3] \
        == (255, 255, 255), "the central block is the floor"
    # The four corner blocks are always empty, and an air neighbour gets no arm.
    assert image.getpixel((x0 + 3, y0 + 3))[3] == 0, "the corner block stays empty"
    assert image.getpixel((x0 + 3, y0 + AREA_INSET + half))[3] == 0, "no arm towards air"
    assert image.getpixel((0, 0))[3] == 0, "the neighbouring cells are air"


def test_area_ratio_walls_sit_on_the_inset_lines() -> None:
    """A walled-in ground cell at 50 % is a small room: block plus four walls."""
    maze = Maze(3, 3, layers=1)
    layer = _styled_layer(maze)
    layer.set_cell(1, 1, CellData(kind=GROUND))
    layer.area_ratio = 50
    for neighbour in ((0, 1), (2, 1), (1, 0), (1, 2)):
        layer.set_edge_state((1, 1), neighbour, "wall")
    image = _renderer(AREA_SIZE).render(maze, 0)
    x0, y0 = AREA_SIZE, AREA_SIZE
    half = AREA_INNER // 2
    mid_y = y0 + AREA_INSET + half
    mid_x = x0 + AREA_INSET + half
    assert image.getpixel((x0 + AREA_INSET, mid_y))[:3] == (0, 0, 0), "left wall"
    assert image.getpixel((x0 + AREA_SIZE - AREA_INSET, mid_y))[:3] == (0, 0, 0), "right wall"
    assert image.getpixel((mid_x, y0 + AREA_INSET))[:3] == (0, 0, 0), "top wall"
    assert image.getpixel((mid_x, y0 + AREA_SIZE - AREA_INSET))[:3] == (0, 0, 0), "bottom wall"
    assert image.getpixel((mid_x, mid_y))[:3] == (255, 255, 255), "the room is floor"
    assert image.getpixel((x0 + 3, y0 + 3))[3] == 0, "nothing is drawn outside the room"


def test_area_ratio_walled_edges_get_no_arms() -> None:
    """A walled edge has floor on neither side: the gap between the walls is empty."""
    size = AREA_SIZE
    maze = Maze(2, 1, layers=1)
    layer = _styled_layer(maze)
    _ground(layer)
    layer.area_ratio = 50
    layer.set_edge_state((0, 0), (1, 0), "wall")
    image = _renderer(size).render(maze, 0)
    mid = size // 2
    # Each cell walls itself off on its *own* inset line ...
    assert image.getpixel((size - AREA_INSET, mid))[:3] == (0, 0, 0), \
        "the left cell's wall moves to x = 30"
    assert image.getpixel((size + AREA_INSET, mid))[:3] == (0, 0, 0), \
        "the right cell's wall moves to x = 50"
    # ... and the floor of each room stops at that wall.
    assert image.getpixel((size - AREA_INSET - 4, mid))[:3] == (255, 255, 255), "left room floor"
    assert image.getpixel((size + AREA_INSET + 4, mid))[:3] == (255, 255, 255), "right room floor"
    # Nothing bridges the two walls: a walled edge draws no arms at all.
    for x in range(size - AREA_INSET + 3, size + AREA_INSET - 2):
        assert image.getpixel((x, mid))[3] == 0, f"no floor may bridge the wall at {x}"
    # The empty corner blocks left and right of the band stay empty.
    assert image.getpixel((size - AREA_INSET // 2, 4))[3] == 0, "corner block stays empty"


def test_area_ratio_one_way_edge_keeps_the_floors_joined() -> None:
    """A one-way edge counts as connected, so both cells draw their arm."""
    size = AREA_SIZE
    maze = Maze(2, 1, layers=1)
    layer = _styled_layer(maze)
    _ground(layer)
    layer.area_ratio = 50
    layer.set_edge((0, 0), (1, 0), True, False)
    image = _renderer(size).render(maze, 0)
    mid = size // 2

    def black(x: int, y: int) -> bool:
        pixel = image.getpixel((x, y))
        return pixel[3] > 0 and pixel[:3] == (0, 0, 0)

    # The two arms meet on the shared border, so the floor is continuous ...
    band = AREA_INSET + 3           # inside the arm, clear of the arrow head
    assert image.getpixel((size - AREA_INSET, band))[:3] == (255, 255, 255), "left arm"
    assert image.getpixel((size + AREA_INSET // 2, band))[:3] == (255, 255, 255), "right arm"
    # ... and the gate itself moved out onto that border, spanning its middle part.
    gate = [y for y in range(size) if black(size, y)]
    assert gate, "the dashed gate must sit on the shared border now"
    assert min(gate) >= AREA_INSET - 2 and max(gate) <= size - AREA_INSET + 2, \
        f"the gate must stay in the middle part of the border: {min(gate)}..{max(gate)}"
    # Only the traveller's cell draws a gate, and its head stays inside that cell.
    middle = range(AREA_INSET + 3, size - AREA_INSET - 3)
    assert not any(black(size - AREA_INSET, y) for y in middle), \
        "no gate may be left in the middle of the inset line"
    assert any(black(size - 6, y) for y in range(size)), \
        "the arrow head must widen to the left of the border"


def test_area_ratio_frames_outline_the_empty_corner_blocks() -> None:
    """All four sides open: the floor is a cross and eight segments remain.

    This is the specification's third example: even a cell that is connected on
    every side keeps its walls, because the corner blocks stay empty.
    """
    size = AREA_SIZE
    maze = Maze(3, 3, layers=1)
    layer = _styled_layer(maze)
    _ground(layer)
    layer.area_ratio = 50
    image = _renderer(size).render(maze, 0)

    def black(x: int, y: int) -> bool:
        pixel = image.getpixel((x, y))
        return pixel[3] > 0 and pixel[:3] == (0, 0, 0)

    # The junction of four cells is an empty square, outlined by their frames:
    # each cell contributes the two parts of its inset lines that touch it.
    assert image.getpixel((size, size))[3] == 0, "the junction stays empty"
    assert black(size - AREA_INSET, size - 5), "the left frame"
    assert black(size + AREA_INSET, size - 5), "the right frame"
    assert black(size - 5, size - AREA_INSET), "the frame above"
    assert black(size - 5, size + AREA_INSET), "the frame below"
    # An open edge draws nothing in the middle of the inset line, so the arms
    # keep the cross connected.
    assert image.getpixel((size + AREA_INSET, size + AREA_INSET + 5))[:3] == (255, 255, 255), \
        "an open edge draws no wall"
    assert image.getpixel((2 * size, size + AREA_INSET + 5))[:3] == (255, 255, 255), \
        "the passage across the shared border stays open"

    # The outer parts outline the corner blocks, so they exist exactly while the
    # arm of the perpendicular side does.  Walling off the edge above (1, 1)
    # removes that arm, hence the two parts that touched its corners.
    a, b = size + AREA_INSET, size + 5          # upper part of (1, 1)'s left line
    c, d = size + AREA_INSET, size + AREA_INSET + AREA_INNER + 5   # lower part
    assert black(a, b) and black(c, d), "both outer parts are drawn while all sides open"
    layer.set_edge_state((1, 1), (1, 0), "wall")
    image = _renderer(size).render(maze, 0)
    assert not black(a, b), "the frame disappears together with the arm above it"
    assert black(c, d), "the frame of the untouched side stays"
    # The walled edge itself is a solid line on (1, 1)'s own inset line.
    assert black(size + AREA_INSET + AREA_INNER // 2, size + AREA_INSET), "the new wall is drawn"


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
