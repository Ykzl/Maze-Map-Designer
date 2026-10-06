"""Headless helpers: build sample mazes and render them to PNG.

The output keeps the real transparency of the render: air cells have no
background of their own, so a viewer shows its own page colour through them.

Examples::

    python cli.py --sample --out exported_png/sample_maze.png
    python cli.py mazes/lcVII.json --out exported_png/preview.png --cell-size 24 --active 1
    python cli.py --sample --out exported_png/floor3.png --active 2 --only-active

Colours accept ``#RRGGBB`` or ``#RRGGBBAA``; in the eight digit form the last two
digits are the opacity of that colour (``FF`` fully visible, ``00`` invisible).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from config import Config
from maze import (
    DEFAULT_AREA_RATIO,
    DEFAULT_FLOOR_COLOR,
    DEFAULT_OBJECT_COLOR,
    DEFAULT_PORTAL_COLOR,
    DEFAULT_WALL_WIDTH,
    Maze,
    build_sample_maze,
    normalise_area_ratio,
    normalise_color,
    normalise_wall_width,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="迷宫地图编辑器 - 命令行渲染工具")
    parser.add_argument("maze", nargs="?", help="迷宫 JSON 文件（省略时使用示例迷宫）")
    parser.add_argument("--sample", action="store_true", help="使用内置示例迷宫")
    parser.add_argument("--out", default=os.path.join("exported_png", "preview.png"),
                        help="输出 PNG 路径（默认 exported_png/preview.png，透明底）")
    parser.add_argument("--active", type=int, default=None, help="当前编辑楼层的栈内下标")
    parser.add_argument("--cell-size", type=int, default=None, help="单元格像素尺寸")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--save-json", default=None, help="把（示例）迷宫另存为 JSON")
    parser.add_argument("--walls-only", action="store_true",
                        help="只绘制墙与单向箭头，不绘制地板/文本/传送门"
                             "（墙的位置仍按单元内容计算）")
    parser.add_argument("--only-active", action="store_true",
                        help="只绘制当前楼层，不把其他楼层合成进来")
    # Appearance is stored per layer in the maze file; these override the values
    # of the layer being rendered.  Colours may carry an opacity:
    # "#RRGGBBAA", the last two digits being the opacity (FF = fully visible).
    parser.add_argument("--object-color", default=None,
                        help=f"覆盖当前层的物件颜色 #RRGGBB[AA]（默认 {DEFAULT_OBJECT_COLOR}）")
    parser.add_argument("--floor-color", default=None,
                        help=f"覆盖当前层的地面颜色 #RRGGBB[AA]（默认 {DEFAULT_FLOOR_COLOR}）")
    parser.add_argument("--portal-color", default=None,
                        help=f"覆盖当前层的传送门颜色 #RRGGBB[AA]（默认 {DEFAULT_PORTAL_COLOR}）")
    parser.add_argument("--wall-width", type=int, default=None,
                        help=f"覆盖当前层的墙宽（默认 {DEFAULT_WALL_WIDTH}）")
    parser.add_argument("--area-ratio", type=int, default=None,
                        help=f"覆盖当前层的面积占比 0~100（默认 {DEFAULT_AREA_RATIO}）")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config)
    if args.cell_size:
        config.cell_size = max(8, args.cell_size)

    if args.maze and not args.sample:
        try:
            maze = Maze.load(args.maze)
        except ValueError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
    else:
        maze = build_sample_maze()

    if args.active is not None:
        maze.active = max(0, min(args.active, maze.layer_count - 1))

    layer = maze.active_layer()
    if args.object_color is not None:
        layer.object_color = normalise_color(args.object_color, layer.object_color)
    if args.floor_color is not None:
        layer.floor_color = normalise_color(args.floor_color, layer.floor_color)
    if args.portal_color is not None:
        layer.portal_color = normalise_color(args.portal_color, layer.portal_color)
    if args.wall_width is not None:
        layer.wall_width = normalise_wall_width(args.wall_width, layer.wall_width)
    if args.area_ratio is not None:
        layer.area_ratio = normalise_area_ratio(args.area_ratio, layer.area_ratio)

    if args.save_json:
        maze.save(args.save_json)
        print(f"已保存迷宫: {args.save_json}")

    from renderer import MazeRenderer  # imported late so --help stays fast

    renderer = MazeRenderer(config)
    # The cells stay in place: they define where the walls are drawn.
    renderer.show_content = not args.walls_only
    renderer.show_only_active = args.only_active
    # The PNG keeps the transparency of the render; no checkerboard behind it.
    image = renderer.render(maze, maze.active)
    folder = os.path.dirname(os.path.abspath(args.out))
    if folder:
        os.makedirs(folder, exist_ok=True)
    image.save(args.out)
    print(f"已输出: {args.out}  ({image.width}x{image.height}, "
          f"{maze.width}x{maze.height}x{maze.layer_count} 单元, 当前 {maze.floor_of(maze.active)} 层"
          f"{'（仅当前层）' if args.only_active else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
