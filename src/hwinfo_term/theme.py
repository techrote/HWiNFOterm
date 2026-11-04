"""Declarative semantic themes and LUTEXskin-style palette generation."""

from __future__ import annotations

import colorsys
import math
import os
import random
import re
from copy import deepcopy
from typing import Any


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ROLE_DEFAULTS = {
    "surface.base": "#0b1016",
    "surface.tile": "#101923",
    "surface.raised": "#182430",
    "text.primary": "#e5edf3",
    "text.secondary": "#a9b8c5",
    "text.disabled": "#6d7c89",
    "text.inverse": "#071014",
    "accent.primary": "#43d6b5",
    "accent.secondary": "#59a9ff",
    "state.selected": "#1c4145",
    "state.focus": "#f0c75e",
    "state.attention": "#ff9d4d",
    "status.success": "#69d391",
    "status.warning": "#f0c75e",
    "status.error": "#ff6f78",
    "border.normal": "#304250",
    "border.strong": "#5b7485",
}

THEMES: dict[str, dict[str, Any]] = {
    "LUTEX Night": {
        "id": "lutex-night",
        "semantic_roles": {},
        "palette": {
            "interpolation": "srgb",
            "cyclic": False,
            "seed": 17,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#11202c", "locked": True, "label": "deep"},
                {"position": 0.28, "colour": "#176b72", "locked": False, "label": "teal"},
                {"position": 0.58, "colour": "#39c49e", "locked": False, "label": "mint"},
                {"position": 0.82, "colour": "#8be0b4", "locked": False, "label": "pale"},
                {"position": 1.0, "colour": "#f1c75b", "locked": True, "label": "gold"},
            ],
        },
        "rules": [],
    },
    "LUTEX Ember": {
        "id": "lutex-ember",
        "semantic_roles": {
            "accent.secondary": "#70b8ff",
            "state.selected": "#492a25",
        },
        "palette": {
            "interpolation": "linear",
            "cyclic": False,
            "seed": 42,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#321c28", "locked": True, "label": "wine"},
                {"position": 0.30, "colour": "#9c3f36", "locked": False, "label": "ember"},
                {"position": 0.61, "colour": "#e87943", "locked": False, "label": "flame"},
                {"position": 0.84, "colour": "#f2bc65", "locked": False, "label": "warm"},
                {"position": 1.0, "colour": "#fff0c2", "locked": True, "label": "light"},
            ],
        },
        "rules": [],
    },
    "Cyberwave": {
        "id": "cyberwave",
        "semantic_roles": {
            "surface.base": "#080b1d",
            "surface.tile": "#10162d",
            "surface.raised": "#192342",
            "text.primary": "#f2f4ff",
            "text.secondary": "#b5c0e8",
            "text.disabled": "#7885aa",
            "text.inverse": "#080b1d",
            "accent.primary": "#38e3f5",
            "accent.secondary": "#ff60cf",
            "state.selected": "#282d5a",
            "state.focus": "#ffe36e",
            "state.attention": "#ff9e61",
            "status.success": "#61e9b5",
            "status.warning": "#ffd35c",
            "status.error": "#ff638b",
            "border.normal": "#34466f",
            "border.strong": "#6177b2",
        },
        "palette": {
            "interpolation": "linear",
            "cyclic": False,
            "seed": 13,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#0b1036", "locked": True, "label": "deep space"},
                {"position": 0.24, "colour": "#3433bb", "locked": False, "label": "ultraviolet"},
                {"position": 0.48, "colour": "#237fe8", "locked": False, "label": "electric blue"},
                {"position": 0.70, "colour": "#18d7d1", "locked": False, "label": "neon cyan"},
                {"position": 0.86, "colour": "#ed48bb", "locked": False, "label": "magenta"},
                {"position": 1.0, "colour": "#ffd2f6", "locked": True, "label": "afterglow"},
            ],
        },
        "rules": [],
    },
    "Corrosion": {
        "id": "corrosion",
        "semantic_roles": {
            "surface.base": "#151713",
            "surface.tile": "#1e211a",
            "surface.raised": "#2b2a20",
            "text.primary": "#eee4cd",
            "text.secondary": "#c2b79a",
            "text.disabled": "#827b64",
            "text.inverse": "#171813",
            "accent.primary": "#78c5a0",
            "accent.secondary": "#d58a52",
            "state.selected": "#34443a",
            "state.focus": "#e7c56c",
            "state.attention": "#dc8244",
            "status.success": "#82c58a",
            "status.warning": "#e8c75e",
            "status.error": "#e87558",
            "border.normal": "#4b4937",
            "border.strong": "#777353",
        },
        "palette": {
            "interpolation": "srgb",
            "cyclic": False,
            "seed": 29,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#30251b", "locked": True, "label": "oxidized iron"},
                {"position": 0.24, "colour": "#805038", "locked": False, "label": "old copper"},
                {"position": 0.47, "colour": "#bd6841", "locked": False, "label": "rust"},
                {"position": 0.71, "colour": "#537e65", "locked": False, "label": "verdigris"},
                {"position": 1.0, "colour": "#c3c797", "locked": True, "label": "aged patina"},
            ],
        },
        "rules": [],
    },
    "High Contrast": {
        "id": "high-contrast",
        "semantic_roles": {
            "surface.base": "#000000",
            "surface.tile": "#000000",
            "surface.raised": "#101010",
            "text.primary": "#ffffff",
            "text.secondary": "#e0e0e0",
            "text.disabled": "#aaaaaa",
            "accent.primary": "#00ffff",
            "accent.secondary": "#ffff00",
            "state.selected": "#003b46",
            "state.focus": "#ffff00",
            "status.success": "#00ff66",
            "status.warning": "#ffff00",
            "status.error": "#ff6262",
            "border.normal": "#bbbbbb",
            "border.strong": "#ffffff",
        },
        "palette": {
            "interpolation": "srgb",
            "cyclic": False,
            "seed": 1,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#000000", "locked": True, "label": "black"},
                {"position": 0.5, "colour": "#00ffff", "locked": False, "label": "cyan"},
                {"position": 1.0, "colour": "#ffffff", "locked": True, "label": "white"},
            ],
        },
        "rules": [],
    },
    "Monochrome": {
        "id": "monochrome",
        "semantic_roles": {
            "surface.base": "#000000",
            "surface.tile": "#000000",
            "surface.raised": "#000000",
            "text.primary": "#ffffff",
            "text.secondary": "#d0d0d0",
            "text.disabled": "#888888",
            "accent.primary": "#ffffff",
            "accent.secondary": "#dddddd",
            "state.selected": "#202020",
            "state.focus": "#ffffff",
            "status.success": "#ffffff",
            "status.warning": "#ffffff",
            "status.error": "#ffffff",
            "border.normal": "#777777",
            "border.strong": "#ffffff",
        },
        "palette": {
            "interpolation": "srgb",
            "cyclic": False,
            "seed": 0,
            "variation": 0,
            "anchors": [
                {"position": 0.0, "colour": "#202020", "locked": True, "label": "shade"},
                {"position": 1.0, "colour": "#ffffff", "locked": True, "label": "light"},
            ],
        },
        "rules": [],
    },
}

TARGETS = {
    ("chrome", "title"),
    ("chrome", "menu"),
    ("workspace", "tile"),
    ("workspace", "tile-title"),
    ("workspace", "divider"),
    ("content", "sensor-row"),
    ("content", "graph"),
    ("status", "bar"),
    ("overlay", "dialog"),
    ("overlay", "selection"),
}

ROLE_RULES = {
    "title": {"foreground": "accent.primary", "background": "surface.base", "bold": True},
    "menu": {"foreground": "text.secondary", "background": "surface.raised"},
    "tile": {"foreground": "text.primary", "background": "surface.tile"},
    "tile-title": {"foreground": "accent.primary", "background": "surface.raised", "bold": True},
    "divider": {"foreground": "border.strong", "background": "surface.base"},
    "sensor-row": {"foreground": "text.primary", "background": "surface.tile"},
    "graph": {"foreground": "accent.primary", "background": "surface.tile"},
    "bar": {"foreground": "text.primary", "background": "surface.raised"},
    "dialog": {"foreground": "text.primary", "background": "surface.raised"},
    "selection": {"foreground": "text.primary", "background": "state.selected", "bold": True},
}


def _rgb(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("Expected a six-digit hexadecimal colour")
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(value: tuple[float, float, float]) -> str:
    vals = tuple(max(0, min(255, round(channel * 255))) for channel in value)
    return "#{:02x}{:02x}{:02x}".format(*vals)


def _to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _to_srgb(value: float) -> float:
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1 / 2.4)) - 0.055


def sample_palette(palette: dict[str, Any], size: int = 256) -> list[str]:
    anchors = palette.get("anchors", [])
    if not isinstance(anchors, list) or not anchors:
        anchors = THEMES["LUTEX Night"]["palette"]["anchors"]
    stops: list[tuple[float, tuple[int, int, int], bool]] = []
    for item in anchors:
        try:
            pos = float(item["position"])
            rgb = _rgb(item["colour"])
            if math.isfinite(pos) and 0 <= pos <= 1:
                stops.append((pos, rgb, bool(item.get("locked", False))))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if not stops:
        stops = [(0.0, (17, 32, 44), True), (1.0, (241, 199, 91), True)]
    stops.sort(key=lambda stop: stop[0])
    if len(stops) == 1:
        stops = [(0.0, stops[0][1], stops[0][2]), (1.0, stops[0][1], stops[0][2])]
    interpolation = palette.get("interpolation", "srgb")
    cyclic = bool(palette.get("cyclic", False))
    if cyclic and stops[-1][0] == 1.0:
        stops[-1] = (stops[-1][0], stops[0][1], stops[-1][2])

    def convert(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
        values = tuple(channel / 255 for channel in rgb)
        return tuple(_to_linear(c) for c in values) if interpolation == "linear" else values

    converted = [(pos, convert(rgb), locked, rgb) for pos, rgb, locked in stops]
    result: list[str] = []
    amount = max(0.0, min(100.0, float(palette.get("variation", 0) or 0))) / 100
    try:
        seed = int(palette.get("seed", 0))
    except (TypeError, ValueError, OverflowError):
        seed = 0
    rng = random.Random(seed)
    count = max(2, int(size))

    for index in range(count):
        t = index / (count - 1)
        pair = None
        for left, right in zip(converted, converted[1:]):
            if left[0] <= t <= right[0]:
                pair = (left, right, t)
                break
        if pair is None and cyclic:
            pair = (converted[-1], converted[0], t + 1)
        if pair is None:
            left, right = (converted[0], converted[1]) if t < converted[0][0] else (converted[-2], converted[-1])
            pair = (left, right, t)
        left, right, position = pair
        start = left[0]
        end = right[0] + (1.0 if cyclic and right[0] <= left[0] else 0.0)
        ratio = 0.0 if end == start else max(0.0, min(1.0, (position - start) / (end - start)))
        colour = tuple(a + (b - a) * ratio for a, b in zip(left[1], right[1]))
        if interpolation == "linear":
            colour = tuple(_to_srgb(c) for c in colour)
        is_locked_stop = (left[2] and abs(t - left[0]) <= 1 / count) or (right[2] and abs(t - right[0]) <= 1 / count)
        if amount and not is_locked_stop:
            # Seeded, bounded RGB jitter. A zero amount leaves the sampled LUT exact.
            amplitude = amount * 0.045
            colour = tuple(max(0.0, min(1.0, c + rng.uniform(-amplitude, amplitude))) for c in colour)
        result.append(_hex(colour))
    return result


class Theme:
    def __init__(self, name: str, definition: dict[str, Any] | None = None, colour_mode: str = "truecolor"):
        self.name = name if name in THEMES else "LUTEX Night"
        self.definition = deepcopy(definition if definition is not None else THEMES[self.name])
        self.colour_mode = colour_mode
        self.palette = self._validate_palette(self.definition.get("palette", {}))
        self.lut = sample_palette(self.palette)
        roles = deepcopy(ROLE_DEFAULTS)
        for role, value in self.definition.get("semantic_roles", {}).items():
            if role in roles and isinstance(value, str) and HEX.fullmatch(value):
                roles[role] = value.lower()
        if "semantic_roles" not in self.definition or not isinstance(self.definition["semantic_roles"], dict):
            self.definition["semantic_roles"] = {}
        palette_index = self.lut[min(255, max(0, int(len(self.lut) * 0.78)))]
        secondary_index = self.lut[min(255, max(0, int(len(self.lut) * 0.55)))]
        if not self.definition["semantic_roles"].get("accent.primary"):
            roles["accent.primary"] = palette_index
        if not self.definition["semantic_roles"].get("accent.secondary"):
            roles["accent.secondary"] = secondary_index
        self.roles = roles
        self.rules = self._validate_rules(self.definition.get("rules", []))

    @staticmethod
    def _validate_palette(raw: Any) -> dict[str, Any]:
        fallback = deepcopy(THEMES["LUTEX Night"]["palette"])
        if not isinstance(raw, dict):
            return fallback
        palette = deepcopy(fallback)
        palette["interpolation"] = raw.get("interpolation") if raw.get("interpolation") in {"srgb", "linear"} else fallback["interpolation"]
        palette["cyclic"] = raw.get("cyclic") if isinstance(raw.get("cyclic"), bool) else fallback["cyclic"]
        try:
            palette["seed"] = int(raw.get("seed", fallback["seed"]))
        except (TypeError, ValueError, OverflowError):
            pass
        try:
            palette["variation"] = max(0, min(100, int(raw.get("variation", 0))))
        except (TypeError, ValueError, OverflowError):
            pass
        anchors = raw.get("anchors")
        valid = []
        if isinstance(anchors, list):
            for anchor in anchors[:32]:
                try:
                    position = float(anchor["position"])
                    colour = str(anchor["colour"])
                    if math.isfinite(position) and 0 <= position <= 1 and HEX.fullmatch(colour):
                        valid.append({
                            "position": position,
                            "colour": colour.lower(),
                            "locked": bool(anchor.get("locked", False)),
                            "label": str(anchor.get("label", ""))[:32],
                        })
                except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                    continue
        if len(valid) >= 2:
            palette["anchors"] = sorted(valid, key=lambda item: item["position"])
        return palette

    @staticmethod
    def _validate_rules(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        valid = []
        for item in raw[:256]:
            if not isinstance(item, dict) or not isinstance(item.get("target"), dict) or not isinstance(item.get("properties"), dict):
                continue
            target = item["target"]
            zone, part = target.get("zone"), target.get("part")
            if not isinstance(zone, str) or not isinstance(part, str):
                continue
            state = target.get("state", "default")
            properties = {}
            for prop in ("foreground", "background", "border", "bold", "accent"):
                value = item["properties"].get(prop)
                if prop == "bold" and isinstance(value, bool):
                    properties[prop] = value
                elif isinstance(value, str) and (value in ROLE_DEFAULTS or HEX.fullmatch(value)):
                    properties[prop] = value.lower() if HEX.fullmatch(value) else value
            if properties:
                valid.append({"target": {"zone": zone, "part": part, "state": state}, "properties": properties})
        return valid

    def resolve(self, zone: str, part: str, state: str = "default") -> dict[str, Any]:
        # LUTEXskin selector order: exact target, part+state, zone+part, part, zone, base.
        candidates = [
            (zone, part, state),
            ("*", part, state),
            (zone, part, "*"),
            ("*", part, "*"),
            (zone, "*", "*"),
        ]
        result: dict[str, Any] = ROLE_RULES.get(part, ROLE_RULES["tile"]).copy()
        for candidate in candidates[::-1]:
            for rule in self.rules:
                target = rule["target"]
                if (target.get("zone", "*"), target.get("part", "*"), target.get("state", "default")) == candidate:
                    result.update(rule["properties"])
        return result

    def colour(self, value: str) -> str:
        if value in self.roles:
            return self.roles[value]
        return value.lower() if isinstance(value, str) and HEX.fullmatch(value) else self.roles["text.primary"]

    def ansi(self, colour: str, foreground: bool = True) -> str:
        if self.colour_mode == "mono":
            return ""
        red, green, blue = _rgb(self.colour(colour))
        prefix = 38 if foreground else 48
        if self.colour_mode == "truecolor":
            return f"\x1b[{prefix};2;{red};{green};{blue}m"
        if self.colour_mode == "256":
            index = _xterm_index(red, green, blue)
            return f"\x1b[{prefix};5;{index}m"
        index = _ansi16_index(red, green, blue)
        return f"\x1b[{(30 if foreground else 40) + (index if index < 8 else index - 8)}m" if index < 8 else f"\x1b[{(90 if foreground else 100) + index - 8}m"


def _xterm_rgb(index: int) -> tuple[int, int, int]:
    if index < 16:
        basic = [
            (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
            (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
            (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        ]
        return basic[index]
    if index >= 232:
        v = 8 + (index - 232) * 10
        return v, v, v
    index -= 16
    levels = [0, 95, 135, 175, 215, 255]
    return levels[index // 36], levels[(index // 6) % 6], levels[index % 6]


def _xterm_index(red: int, green: int, blue: int) -> int:
    # Deterministic nearest-colour search across the canonical xterm 256 palette.
    return min(range(256), key=lambda i: sum((a - b) ** 2 for a, b in zip((red, green, blue), _xterm_rgb(i))))


def _ansi16_index(red: int, green: int, blue: int) -> int:
    return min(range(16), key=lambda i: sum((a - b) ** 2 for a, b in zip((red, green, blue), _xterm_rgb(i))))


def detect_colour_mode(preference: str = "auto") -> str:
    if preference in {"truecolor", "256", "16", "mono"}:
        return preference
    term = os.environ.get("TERM", "").lower()
    if os.environ.get("WT_SESSION") or "truecolor" in os.environ.get("COLORTERM", "").lower() or "24bit" in os.environ.get("COLORTERM", "").lower():
        return "truecolor"
    if "256color" in term:
        return "256"
    return "16" if os.name == "nt" or term not in {"", "dumb"} else "mono"


def theme_names() -> list[str]:
    return list(THEMES)


def make_theme(name: str, custom: dict[str, Any] | None = None, colour_mode: str = "truecolor") -> Theme:
    base_name = name if name in THEMES else "LUTEX Night"
    definition = deepcopy(THEMES[base_name])
    if isinstance(custom, dict) and custom.get("theme") == base_name:
        # User changes are data-only palette/role overrides. Unknown properties are ignored.
        if isinstance(custom.get("palette"), dict):
            definition["palette"] = custom["palette"]
        if isinstance(custom.get("semantic_roles"), dict):
            definition["semantic_roles"].update(custom["semantic_roles"])
        if isinstance(custom.get("rules"), list):
            definition["rules"] = custom["rules"]
    return Theme(base_name, definition, colour_mode)


def theme_document(theme: Theme) -> dict[str, Any]:
    return {
        "schema": "lutexskin/1.0",
        "theme": theme.name,
        "semantic_roles": deepcopy(theme.definition.get("semantic_roles", {})),
        "palette": deepcopy(theme.palette),
        "rules": deepcopy(theme.rules),
    }


def set_anchor_colour(palette: dict[str, Any], index: int, colour: str) -> dict[str, Any]:
    if not HEX.fullmatch(colour):
        raise ValueError("Use a colour such as #35c9a5")
    result = deepcopy(palette)
    anchors = result.get("anchors", [])
    if not 0 <= index < len(anchors):
        raise IndexError("No palette anchor is selected")
    anchors[index]["colour"] = colour.lower()
    return result


def shift_anchor_hue(palette: dict[str, Any], index: int, amount: float) -> dict[str, Any]:
    result = deepcopy(palette)
    anchors = result.get("anchors", [])
    if not 0 <= index < len(anchors):
        raise IndexError("No palette anchor is selected")
    red, green, blue = _rgb(anchors[index]["colour"])
    hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
    hue = (hue + amount) % 1.0
    anchors[index]["colour"] = _hex(colorsys.hsv_to_rgb(hue, saturation, value))
    return result
