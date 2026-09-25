"""Configuration loading for the maze map designer.

The whole application is driven by ``config.json`` sitting next to the source
files.  Everything has a sane default, so a missing or partially broken config
file never prevents the editor from starting.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

RGB = Tuple[int, int, int]

#: Global configuration.  Everything that describes the *look of a single
#: floor* -- object colour, floor colour, portal colour and wall width -- lives
#: in the maze file instead, one value per layer (see ``maze.Layer``), because it
#: is edited per floor in the toolbar's third row.
DEFAULT_CONFIG: Dict[str, Any] = {
    "wall": {"dash": [6, 4], "arrow_length": 0.22, "arrow_width": 3},
    "cell_size": 40,
    "text": {"font_family": "Microsoft YaHei", "min_size": 5, "fill_ratio": 0.88},
}

#: Keys that used to live in ``config.json`` and are now stored per layer.
#: They are dropped while loading so an old config file still works.
MOVED_TO_LAYER_KEYS: Tuple[str, ...] = (
    "object_color_group", "floor_color_group", "portal_color",
    "color_group", "wall_width",
)

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
    """Parsed global configuration with convenience accessors.

    Only maze-wide settings live here.  The look of a single floor (colours and
    wall width) is part of the maze document and is read from the layer itself.
    """

    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        given = dict(data or {})
        # Keys that moved into the maze file are ignored, not merged, so an old
        # config.json keeps working without changing how floors look.
        for key in MOVED_TO_LAYER_KEYS:
            given.pop(key, None)
        merged = _deep_merge(DEFAULT_CONFIG, given)
        self.data = merged

        self.wall: Dict[str, Any] = dict(merged["wall"])
        # ``width`` moved to the layer; drop a stale global value if present.
        self.wall.pop("width", None)
        self.text: Dict[str, Any] = dict(merged["text"])
        try:
            self.cell_size: int = max(8, int(merged["cell_size"]))
        except (TypeError, ValueError):
            self.cell_size = DEFAULT_CONFIG["cell_size"]

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
