"""Headless helpers: build sample mazes and render them to PNG.

Examples::

    python cli.py --sample --out sample_maze.png
    python cli.py maze.json --out preview.png --cell-size 24 --active 1
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from config import Config
from maze import (
    DEFAULT_FLOOR_COLOR,
    DEFAULT_OBJECT_COLOR,
    DEFAULT_PORTAL_COLOR,
    DEFAULT_WALL_WIDTH,
    Maze,
    build_sample_maze,
    normalise_color,
    normalise_wall_width,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="迷宫地图编辑器 - 命令行渲染工具")
    parser.add_argument("maze", nargs="?", help="迷宫 JSON 文件（省略时使用示例迷宫）")
    parser.add_argument("--sample", action="store_true", help="使用内置示例迷宫")
    parser.add_argument("--out", default="preview.png", help="输出 PNG 路径")
    parser.add_argument("--active", type=int, default=None, help="当前编辑楼层的栈内下标")
    parser.add_argument("--cell-size", type=int, default=None, help="单元格像素尺寸")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--save-json", default=None, help="把（示例）迷宫另存为 JSON")
    parser.add_argument("--checkerboard", action="store_true",
                        help="在棋盘背景上合成，便于观察透明区域")
    parser.add_argument("--walls-only", action="store_true",
                        help="只绘制墙与单向箭头，不绘制任何单元内容（便于核对墙的位置）")
    # Appearance is stored per layer in the maze file; these override the values
    # of the layer being rendered.
    parser.add_argument("--object-color", default=None,
                        help=f"覆盖当前层的物件颜色（默认 {DEFAULT_OBJECT_COLOR}）")
    parser.add_argument("--floor-color", default=None,
                        help=f"覆盖当前层的地面颜色（默认 {DEFAULT_FLOOR_COLOR}）")
    parser.add_argument("--portal-color", default=None,
                        help=f"覆盖当前层的传送门颜色（默认 {DEFAULT_PORTAL_COLOR}）")
    parser.add_argument("--wall-width", type=int, default=None,
                        help=f"覆盖当前层的墙宽（默认 {DEFAULT_WALL_WIDTH}）")
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

    if args.walls_only:
        for layer in maze.layers:
            layer.cells.clear()

    if args.save_json:
        maze.save(args.save_json)
        print(f"已保存迷宫: {args.save_json}")

    from renderer import MazeRenderer  # imported late so --help stays fast

    renderer = MazeRenderer(config)
    image = (renderer.render_on_checkerboard(maze, maze.active)
             if args.checkerboard else renderer.render(maze, maze.active))
    image.save(args.out)
    print(f"已输出: {args.out}  ({image.width}x{image.height}, "
          f"{maze.width}x{maze.height}x{maze.layer_count} 单元, 当前 {maze.floor_of(maze.active)} 层)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
