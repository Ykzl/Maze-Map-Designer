"""Configuration loading for the maze map designer.

The whole application is driven by ``config.json`` sitting next to the source
files.  Everything has a sane default, so a missing or partially broken config
file never prevents the editor from starting.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

RGB = Tuple[int, int, int]

#: Default palette used to colour the *objects* of a floor (walls, text, and the
#: outline drawn around the floor being edited).  Index 0 is floor 1, index 1 is
#: floor 2, ...  When the floor number exceeds the palette length the palette is
#: used cyclically, so floor 9 is 黑 again.
DEFAULT_OBJECT_COLOR_GROUP: List[str] = [
    "#000000",  # 黑
    "#FF0000",  # 红
    "#FF7F00",  # 橙
    "#FFFF00",  # 黄
    "#00FF00",  # 绿
    "#00FFFF",  # 青
    "#0000FF",  # 蓝
    "#8000FF",  # 紫
]

#: Default palette used to colour the *floor tiles* of a floor.  It follows
#: exactly the same cyclic rule as :data:`DEFAULT_OBJECT_COLOR_GROUP`; the
#: default is a single white entry, i.e. every floor is plain white until the
#: user asks for something else.
DEFAULT_FLOOR_COLOR_GROUP: List[str] = [
    "#FFFFFF",  # 白
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "object_color_group": DEFAULT_OBJECT_COLOR_GROUP,
    "floor_color_group": DEFAULT_FLOOR_COLOR_GROUP,
    "portal_color": "#FF00FF",
    "wall": {"width": 3, "dash": [6, 4], "arrow_length": 0.22, "arrow_width": 3},
    "cell_size": 40,
    "text": {"font_family": "Microsoft YaHei", "min_size": 5, "fill_ratio": 0.88},
}

#: Old name of ``object_color_group``, still accepted when reading a config file.
LEGACY_COLOR_GROUP_KEY = "color_group"

#: Filename used by :func:`save_config` / the "重载配置" toolbar button.
CONFIG_FILENAME = "config.json"


def default_config_path() -> str:
    """Path of ``config.json`` next to this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def hex_to_rgb(value: str) -> RGB:
    """``"#RRGGBB"`` (or ``"#RGB"``) -> ``(r, g, b)``.

    Raises:
        ValueError: if the string is not a usable colour literal.
    """
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"not a #RRGGBB colour: {value!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _colour_group(value: Any, fallback: List[str]) -> List[str]:
    """Validate a colour list, falling back to ``fallback`` when unusable."""
    if isinstance(value, str):  # a single colour is accepted as a 1-entry group
        value = [value]
    group = [str(c) for c in (value or []) if str(c).strip()]
    try:
        for colour in group:
            hex_to_rgb(colour)
    except ValueError:
        return list(fallback)
    return group or list(fallback)


def _cycle(palette: List[str], floor: int) -> str:
    """Pick ``floor``'s colour from ``palette``, walking it cyclically.

    Floors are numbered from 1 upwards.  Negative numbers are still mapped
    (walking the palette backwards) so the helper stays total, but the editor
    itself only produces floors ``>= 1``.
    """
    size = len(palette)
    number = int(floor)
    if number >= 1:
        index = (number - 1) % size
    elif number == 0:
        index = 0
    else:
        index = (size - (-number) % size) % size
    return palette[index]


class Config:
    """Parsed configuration with convenience accessors."""

    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        merged = _deep_merge(DEFAULT_CONFIG, data or {})
        self.data = merged

        # ``color_group`` was the original name of the object palette.
        object_group = merged.get("object_color_group")
        if LEGACY_COLOR_GROUP_KEY in (data or {}) and \
                "object_color_group" not in (data or {}):
            object_group = (data or {})[LEGACY_COLOR_GROUP_KEY]
        self.object_color_group: List[str] = _colour_group(
            object_group, DEFAULT_OBJECT_COLOR_GROUP)
        self.floor_color_group: List[str] = _colour_group(
            merged.get("floor_color_group"), DEFAULT_FLOOR_COLOR_GROUP)

        try:
            self.portal_color: str = str(merged["portal_color"])
            hex_to_rgb(self.portal_color)
        except ValueError:
            self.portal_color = DEFAULT_CONFIG["portal_color"]

        self.wall: Dict[str, Any] = dict(merged["wall"])
        self.text: Dict[str, Any] = dict(merged["text"])
        try:
            self.cell_size: int = max(8, int(merged["cell_size"]))
        except (TypeError, ValueError):
            self.cell_size = DEFAULT_CONFIG["cell_size"]

    # -- palettes --------------------------------------------------------
    @property
    def object_group_size(self) -> int:
        return len(self.object_color_group)

    @property
    def floor_group_size(self) -> int:
        return len(self.floor_color_group)

    def object_color_for_floor(self, floor: int) -> str:
        """Colour of the walls / text / outline of ``floor``."""
        return _cycle(self.object_color_group, floor)

    def floor_color_for_floor(self, floor: int) -> str:
        """Background colour of the floor tiles of ``floor``."""
        return _cycle(self.floor_color_group, floor)

    # -- backwards compatible names --------------------------------------
    @property
    def color_group(self) -> List[str]:
        """Alias of :attr:`object_color_group` (the original name)."""
        return self.object_color_group

    @property
    def group_size(self) -> int:
        return self.object_group_size

    def color_for_floor(self, floor: int) -> str:
        """Alias of :meth:`object_color_for_floor`."""
        return self.object_color_for_floor(floor)

    def color_for_layer(self, layer_index: int, lowest_floor: int = 1) -> str:
        """Object colour of the layer at ``layer_index``."""
        return self.object_color_for_floor(lowest_floor + layer_index)

    # -- persistence -----------------------------------------------------
    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        """Load configuration, writing the defaults out when the file is absent."""
        target = path or default_config_path()
        if not os.path.exists(target):
            try:
                save_config(DEFAULT_CONFIG, target)
            except OSError:
                pass
            return cls({})
        try:
            with open(target, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return cls({})
        if not isinstance(raw, dict):
            return cls({})
        return cls(raw)


def save_config(data: Dict[str, Any], path: str | None = None) -> str:
    """Write ``data`` to ``path`` (default: next to this module) as JSON."""
    target = path or default_config_path()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return target
