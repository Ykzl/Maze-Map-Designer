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

#: Default palette used to colour floors.  Index 0 is floor 1, index 1 is
#: floor 2, ...  When the absolute floor number exceeds the palette length the
#: palette is used cyclically.  Underground floors walk the palette backwards,
#: so floor -1 is the last colour, floor -2 the second to last, and so on.
DEFAULT_COLOR_GROUP: List[str] = [
    "#000000",  # 黑
    "#FF0000",  # 红
    "#FF7F00",  # 橙
    "#FFFF00",  # 黄
    "#00FF00",  # 绿
    "#00FFFF",  # 青
    "#0000FF",  # 蓝
    "#8000FF",  # 紫
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "color_group": DEFAULT_COLOR_GROUP,
    "portal_color": "#FF00FF",
    "wall": {"width": 3, "dash": [6, 4], "arrow_length": 0.22, "arrow_width": 3},
    "cell_size": 40,
    "text": {"font_family": "Microsoft YaHei", "min_size": 5, "fill_ratio": 0.88},
}

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


class Config:
    """Parsed configuration with convenience accessors."""

    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        merged = _deep_merge(DEFAULT_CONFIG, data or {})
        self.data = merged

        group = [str(c) for c in merged.get("color_group") or [] if str(c).strip()]
        # Validate eagerly, fall back to the default palette when unusable.
        try:
            for colour in group:
                hex_to_rgb(colour)
        except ValueError:
            group = list(DEFAULT_COLOR_GROUP)
        self.color_group: List[str] = group or list(DEFAULT_COLOR_GROUP)

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

    # -- palette ---------------------------------------------------------
    @property
    def group_size(self) -> int:
        return len(self.color_group)

    def color_for_floor(self, floor: int) -> str:
        """Colour of ``floor`` by walking the palette cyclically.

        Floor numbers are 1-based above ground and negative underground; floor
        0 is not a real floor and is treated as floor 1.  Negative floors walk
        the palette backwards (floor -1 -> last colour, floor -2 -> second to
        last, ...), exactly as required by the specification.
        """
        size = self.group_size
        number = int(floor)
        if number >= 1:
            index = (number - 1) % size
        elif number == 0:
            index = 0
        else:
            index = (size - (-number) % size) % size
        return self.color_group[index]

    def color_for_layer(self, layer_index: int, lowest_floor: int) -> str:
        """Colour of the layer at ``layer_index`` given the lowest floor number."""
        return self.color_for_floor(lowest_floor + layer_index)

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
