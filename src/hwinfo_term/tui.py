"""Windows Terminal dashboard, tile workspace, command router, and watch UI."""

from __future__ import annotations

import copy
import datetime as dt
import math
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import HISTORY_CHART_STYLES, MAX_HISTORY_CHART_ROWS, TILES, save_config
from .model import DemoThread, ReaderThread, SensorRecord, SourceStatus, TelemetryStore
from .terminal import Canvas, Event, TerminalSession, cell_width
from .theme import Theme, detect_colour_mode, make_theme, shift_anchor_hue, theme_document, theme_names


@dataclass(frozen=True, slots=True)
class CommandSpec:
    id: str
    label: str
    category: str
    keys: tuple[str, ...]
    description: str
    pointer_routes: tuple[str, ...] = ("command-palette",)


@dataclass(frozen=True, slots=True)
class HitRegion:
    x: int
    y: int
    width: int
    height: int
    action: str
    payload: Any = None

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


COMMANDS = [
    CommandSpec("view.dashboard", "Dashboard", "View", ("1",), "Show the tile workspace."),
    CommandSpec("view.sensors", "All sensors", "View", ("2",), "Browse and pin live HWiNFO readings."),
    CommandSpec("view.history", "History", "View", ("3",), "Inspect a selected reading over time."),
    CommandSpec("view.recent", "Recent History grid", "View", ("7",), "Show only recent-history metrics in a selectable grid."),
    CommandSpec("view.alerts", "Alerts and events", "View", ("4",), "Review threshold watches and event history."),
    CommandSpec("view.theme", "Theme and palette", "View", ("5",), "Choose a semantic theme and inspect its LUT."),
    CommandSpec("view.source", "HWiNFO source", "View", ("6",), "Show provider connection and sample health."),
    CommandSpec("view.help", "Help", "View", ("?",), "Show keyboard, mouse, and feature help."),
    CommandSpec("command_palette.open", "Command palette", "Navigate", ("ctrl+p",), "Search and invoke every application command."),
    CommandSpec("app.quit", "Quit HWiNFOterm", "Application", ("q", "ctrl+c"), "Close the TUI and restore the terminal."),
    CommandSpec("tile.focus_next", "Focus next tile", "Tiles", ("tab",), "Move focus through visible tiles."),
    CommandSpec("tile.maximize", "Maximize or restore tile", "Tiles", ("m",), "Give the focused tile the full workspace."),
    CommandSpec("tile.hide", "Hide focused tile", "Tiles", ("h",), "Collapse the focused tile while keeping its state."),
    CommandSpec("tile.restore_all", "Restore hidden tiles", "Tiles", ("r",), "Show all dashboard tiles again."),
    CommandSpec("tile.swap_next", "Move focused tile", "Tiles", ("v",), "Move the focused tile one position in the workspace."),
    CommandSpec("layout.reset", "Reset workspace layout", "Layout", ("0",), "Restore the default 2×2 tile arrangement."),
    CommandSpec("layout.column_less", "Narrow left column", "Layout", ("[",), "Adjust the dashboard's vertical split."),
    CommandSpec("layout.column_more", "Widen left column", "Layout", ("]",), "Adjust the dashboard's vertical split."),
    CommandSpec("layout.row_less", "Shorten top row", "Layout", ("-",), "Adjust the dashboard's horizontal split."),
    CommandSpec("layout.row_more", "Heighten top row", "Layout", ("=",), "Adjust the dashboard's horizontal split."),
    CommandSpec("sensor.filter", "Filter sensors", "Sensors", ("/",), "Search sensor and reading names."),
    CommandSpec("sensor.pin_toggle", "Pin or unpin selected sensor", "Sensors", ("p",), "Add or remove a reading from dashboard metrics."),
    CommandSpec("metrics.toggle_bar_labels", "Toggle metric labels inside bars", "Metrics", ("b",), "Show each live metric title and value inside its bar."),
    CommandSpec("history.group_manage", "Manage recent-history groups", "History", ("g",), "Create groups and choose which recent metrics they contain."),
    CommandSpec("history.group_prev", "Previous recent-history group", "History", ("left",), "Show the previous recent-history group."),
    CommandSpec("history.group_next", "Next recent-history group", "History", ("right",), "Show the next recent-history group."),
    CommandSpec("history.columns_less", "Fewer recent-history columns", "History", ("[",), "Reduce the recent-history grid to fewer columns."),
    CommandSpec("history.columns_more", "More recent-history columns", "History", ("]",), "Increase the recent-history grid to more columns."),
    CommandSpec("history.chart_rows_less", "Shorter selected history chart", "History", (",",), "Show fewer vertical rows for the selected metric's history chart."),
    CommandSpec("history.chart_rows_more", "Taller selected history chart", "History", (".",), "Show more vertical rows for the selected metric's history chart."),
    CommandSpec("history.chart_style_next", "Next selected history chart style", "History", (";",), "Cycle the selected metric through block, stem, and point charts."),
    CommandSpec("history.stats_averages_toggle", "Toggle rolling history averages", "History", ("a",), "Show or hide 1-minute and 5-minute rolling averages in chart readouts."),
    CommandSpec("watch.add", "Add threshold watch", "Alerts", ("w",), "Create a hysteresis watch for the selected sensor."),
    CommandSpec("watch.delete", "Remove selected watch", "Alerts", ("x",), "Remove the selected threshold watch."),
    CommandSpec("theme.next", "Next theme", "Theme", ("t",), "Cycle through the included semantic themes."),
    CommandSpec("theme.editor", "Edit palette", "Theme", ("e",), "Open the LUTEXskin-inspired palette composer."),
    CommandSpec("theme.cycle", "Toggle cyclic palette", "Theme", ("c",), "Wrap the palette's endpoint interpolation."),
    CommandSpec("theme.interpolation", "Toggle interpolation", "Theme", ("i",), "Switch between sRGB and linear-light interpolation."),
    CommandSpec("theme.anchor_prev", "Select previous palette anchor", "Theme", ("left",), "Move selection to the previous palette stop."),
    CommandSpec("theme.anchor_next", "Select next palette anchor", "Theme", ("right",), "Move selection to the next palette stop."),
    CommandSpec("theme.hue_down", "Shift anchor hue left", "Theme", ("down",), "Adjust the selected anchor hue."),
    CommandSpec("theme.hue_up", "Shift anchor hue right", "Theme", ("up",), "Adjust the selected anchor hue."),
    CommandSpec("theme.variation_down", "Reduce palette variation", "Theme", ("-",), "Reduce seeded variation while preserving source anchors."),
    CommandSpec("theme.variation_up", "Increase palette variation", "Theme", ("+",), "Increase seeded, reproducible palette variation."),
    CommandSpec("theme.anchor_add", "Add palette anchor", "Theme", ("a",), "Add a colour stop at an available palette position."),
    CommandSpec("theme.anchor_remove", "Remove palette anchor", "Theme", ("x",), "Remove the selected unlocked colour stop."),
    CommandSpec("theme.anchor_lock", "Lock or unlock palette anchor", "Theme", ("l",), "Protect the selected anchor from editing."),
    CommandSpec("theme.anchor_position_left", "Move anchor left", "Theme", (",",), "Move the selected unlocked anchor toward position zero."),
    CommandSpec("theme.anchor_position_right", "Move anchor right", "Theme", (".",), "Move the selected unlocked anchor toward position one."),
    CommandSpec("theme.anchor_edit", "Edit anchor hex colour", "Theme", ("h",), "Enter an exact six-digit hex colour for the selected stop."),
    CommandSpec("theme.seed_next", "Change variation seed", "Theme", ("s",), "Advance the reproducible palette variation seed."),
    CommandSpec("theme.commit", "Commit palette preview", "Theme", ("enter",), "Save the current palette edits."),
    CommandSpec("theme.cancel", "Cancel palette preview", "Theme", ("escape",), "Discard uncommitted palette edits."),
]


def _registry() -> dict[str, CommandSpec]:
    result = {item.id: item for item in COMMANDS}
    if len(result) != len(COMMANDS):
        raise RuntimeError("Command IDs must be unique")
    if any(not item.pointer_routes for item in COMMANDS):
        raise RuntimeError("Every command must remain available from the pointer-operated palette")
    return result


COMMAND_REGISTRY = _registry()


class TUIApp:
    def __init__(
        self,
        state_dir: Path,
        config: dict[str, Any],
        config_error: str | None = None,
        demo: bool = False,
        colour_mode: str | None = None,
        ascii_only: bool | None = None,
        mouse: bool | None = None,
    ):
        self.state_dir = state_dir
        self.config = config
        self.config_error = config_error
        self.demo = demo
        self.colour_mode = colour_mode or detect_colour_mode(config.get("colour_mode", "auto"))
        self.ascii_only = config.get("ascii", False) if ascii_only is None else ascii_only
        self.mouse_enabled = config.get("mouse", True) if mouse is None else mouse
        if not self.mouse_enabled:
            self.config["mouse"] = False
        self.store = TelemetryStore(config.get("history_seconds", 900), config.get("stale_after", 5.0))
        self.store.set_limits(config.get("history_seconds", 900), config.get("stale_after", 5.0))
        self.reader = DemoThread(self.store, config.get("poll_interval", 0.75)) if demo else ReaderThread(self.store, config.get("poll_interval", 0.75))
        self.view = "dashboard"
        self.overlay: str | None = None
        self.overlay_query = ""
        self.overlay_index = 0
        self.watch_form: dict[str, Any] | None = None
        self.watch_form_field = 0
        self.watch_delete_index = 0
        self.focused_tile = config.get("focused_tile", "metrics")
        self.tile_scroll = {tile: 0 for tile in TILES}
        self.tile_order = [tile for tile in config.get("tile_order", TILES) if tile in TILES]
        for tile in TILES:
            if tile not in self.tile_order:
                self.tile_order.append(tile)
        self.hidden_tiles = set(config.get("hidden_tiles", []))
        self.maximized_tile = config.get("maximized_tile")
        self.column_ratio = float(config.get("column_ratio", 0.5))
        self.row_ratio = float(config.get("row_ratio", 0.54))
        self.selected_sensor = config.get("selected_sensor")
        self.selected_sensor_index = 0
        self.filter_text = ""
        self.last_click: tuple[float, str, Any] | None = None
        self.last_right_click: tuple[float, str, str] | None = None
        self.group_metric_index = 0
        self.group_name_mode: str | None = None
        self.group_delete_name: str | None = None
        self.watch_index = int(config.get("watch_index", 0))
        self.theme_name = config.get("theme", "LUTEX Night")
        if self.theme_name not in theme_names():
            self.theme_name = "LUTEX Night"
            self.status_message = "Unknown saved theme; using LUTEX Night"
            self.status_deadline = time.monotonic() + 6
        self.theme_custom = copy.deepcopy(config.get("theme_document", {}))
        self.theme = make_theme(self.theme_name, self.theme_custom, self.colour_mode)
        self.theme_editing = False
        self.theme_before_edit: dict[str, Any] | None = None
        self.theme_working: dict[str, Any] | None = None
        self.theme_anchor_index = 0
        self.theme_colour_draft = "#"
        self.quit_requested = False
        self.hits: list[HitRegion] = []
        self.previous_frame = None
        self.previous_size: tuple[int, int] | None = None
        self.drag: dict[str, Any] | None = None
        warning = "Unknown saved theme; using LUTEX Night" if config.get("theme", "LUTEX Night") not in theme_names() else ""
        self.status_message = config_error or warning
        self.status_deadline = time.monotonic() + (8.0 if self.status_message else 0.0)
        self._sample_count = 0
        self._source_mode = "demo" if demo else "hwinfo"

    def _save(self) -> None:
        self.config["tile_order"] = self.tile_order.copy()
        self.config["hidden_tiles"] = [tile for tile in self.tile_order if tile in self.hidden_tiles]
        self.config["focused_tile"] = self.focused_tile
        self.config["maximized_tile"] = self.maximized_tile
        self.config["column_ratio"] = self.column_ratio
        self.config["row_ratio"] = self.row_ratio
        self.config["selected_sensor"] = self.selected_sensor
        self.config["filter_text"] = self.filter_text
        self.config["watch_index"] = self.watch_index
        self.config["theme"] = self.theme_name
        self.config["theme_document"] = copy.deepcopy(self.theme_custom)
        self.config["ascii"] = self.ascii_only
        self.config["mouse"] = self.mouse_enabled
        self.config["colour_mode"] = self.colour_mode
        try:
            save_config(self.state_dir, self.config)
        except (OSError, TypeError, ValueError) as exc:
            self._message(f"Could not save settings: {exc}", 8)

    def _message(self, value: str, seconds: float = 3.5) -> None:
        self.status_message = value
        self.status_deadline = time.monotonic() + seconds

    def _new_theme(self) -> None:
        self.theme = make_theme(self.theme_name, self.theme_custom, self.colour_mode)
        self.previous_frame = None

    def _style(self, zone: str, part: str, state: str = "default", foreground: str = "text.primary", background: str = "surface.base") -> tuple[str, str, bool, str]:
        resolved = self.theme.resolve(zone, part, state)
        return (
            resolved.get("foreground", foreground),
            resolved.get("background", background),
            bool(resolved.get("bold", False)),
            resolved.get("border", "border.normal"),
        )

    def _tile_focus(self, tile: str) -> None:
        if tile not in TILES:
            return
        self.hidden_tiles.discard(tile)
        self.focused_tile = tile

    def _visible_tiles(self) -> list[str]:
        return [tile for tile in self.tile_order if tile not in self.hidden_tiles]

    def _dispatch(self, command_id: str) -> None:
        if command_id not in COMMAND_REGISTRY:
            return
        if self.overlay == "palette":
            self.overlay = None
            self.overlay_query = ""
        if command_id.startswith("view."):
            self.view = command_id.split(".", 1)[1]
            if self.view in TILES:
                self._tile_focus(self.view)
            if self.view == "theme":
                self._new_theme()
            return
        if command_id.startswith(("tile.", "layout.")):
            self.view = "dashboard"
        if command_id == "command_palette.open":
            self.overlay = "palette"
            self.overlay_query = ""
            self.overlay_index = 0
        elif command_id == "app.quit":
            self.quit_requested = True
        elif command_id == "tile.focus_next":
            visible = self._visible_tiles()
            if visible:
                index = visible.index(self.focused_tile) if self.focused_tile in visible else -1
                self.focused_tile = visible[(index + 1) % len(visible)]
        elif command_id == "tile.maximize":
            self._maximize_tile(self.focused_tile)
        elif command_id == "tile.hide":
            self._hide_tile(self.focused_tile)
        elif command_id == "tile.restore_all":
            self.hidden_tiles.clear()
            self._save()
            self._message("All dashboard tiles restored")
        elif command_id == "tile.swap_next":
            if self.focused_tile in self.tile_order:
                index = self.tile_order.index(self.focused_tile)
                other = (index + 1) % len(self.tile_order)
                self.tile_order[index], self.tile_order[other] = self.tile_order[other], self.tile_order[index]
                self._save()
        elif command_id == "layout.reset":
            self.tile_order = TILES.copy()
            self.hidden_tiles.clear()
            self.focused_tile = "metrics"
            self.maximized_tile = None
            self.column_ratio = 0.5
            self.row_ratio = 0.54
            self._save()
            self._message("Workspace reset to Live 2×2")
        elif command_id == "layout.column_less":
            self.column_ratio = max(0.22, self.column_ratio - 0.04)
            self._save()
        elif command_id == "layout.column_more":
            self.column_ratio = min(0.78, self.column_ratio + 0.04)
            self._save()
        elif command_id == "layout.row_less":
            self.row_ratio = max(0.22, self.row_ratio - 0.04)
            self._save()
        elif command_id == "layout.row_more":
            self.row_ratio = min(0.78, self.row_ratio + 0.04)
            self._save()
        elif command_id == "sensor.filter":
            self.overlay = "filter"
            self.overlay_query = self.filter_text
        elif command_id == "sensor.pin_toggle":
            self._toggle_pin()
        elif command_id == "metrics.toggle_bar_labels":
            self.config["metric_bar_labels"] = not bool(self.config.get("metric_bar_labels", False))
            self._save()
            state = "on" if self.config["metric_bar_labels"] else "off"
            self._message(f"Metric title and value inside bars: {state}")
        elif command_id == "history.group_manage":
            self._open_history_group_manager()
        elif command_id == "history.group_prev":
            self._cycle_history_group(-1)
        elif command_id == "history.group_next":
            self._cycle_history_group(1)
        elif command_id == "history.columns_less":
            self._set_history_columns(-1)
        elif command_id == "history.columns_more":
            self._set_history_columns(1)
        elif command_id == "history.chart_rows_less":
            self._set_history_chart_rows(-1)
        elif command_id == "history.chart_rows_more":
            self._set_history_chart_rows(1)
        elif command_id == "history.chart_style_next":
            self._cycle_history_chart_style()
        elif command_id == "history.stats_averages_toggle":
            self._toggle_history_averages()
        elif command_id == "watch.add":
            self._open_watch_form()
        elif command_id == "watch.delete":
            if not self.config.get("watches"):
                self._message("There are no threshold watches to remove")
            else:
                self.overlay = "watch_delete"
                self.watch_delete_index = min(self.watch_index, len(self.config["watches"]) - 1)
        elif command_id == "theme.next":
            if self.theme_editing:
                self._message("Commit or cancel the palette preview before changing themes")
                return
            names = theme_names()
            self.theme_name = names[(names.index(self.theme_name) + 1) % len(names)] if self.theme_name in names else names[0]
            self.theme_custom = {}
            self._new_theme()
            self._save()
            self._message(f"Theme: {self.theme_name}")
        elif command_id == "theme.editor":
            self._open_theme_editor()
        elif command_id.startswith("theme.") and self.theme_editing:
            self._edit_theme(command_id)

    def _toggle_pin(self) -> None:
        if not self.selected_sensor:
            self._message("Select a sensor first")
            return
        pinned = self.config.setdefault("pinned", [])
        if self.selected_sensor in pinned:
            pinned.remove(self.selected_sensor)
            self._message("Sensor unpinned")
        else:
            if len(pinned) >= 32:
                self._message("Dashboard supports up to 32 pinned sensors")
                return
            pinned.append(self.selected_sensor)
            excluded = self.config.setdefault("excluded_metrics", [])
            if self.selected_sensor in excluded:
                excluded.remove(self.selected_sensor)
            self._message("Sensor pinned to dashboard metrics")
        self._save()

    def _maximize_tile(self, tile: str) -> None:
        if tile not in TILES:
            return
        self._tile_focus(tile)
        self.maximized_tile = None if self.maximized_tile == tile else tile
        self._save()

    def _hide_tile(self, tile: str) -> None:
        if tile not in TILES:
            return
        self._tile_focus(tile)
        visible = self._visible_tiles()
        if len(visible) <= 1:
            self._message("Keep at least one tile visible; use the tile manager to restore others.")
            return
        self.hidden_tiles.add(tile)
        self.maximized_tile = None
        self.focused_tile = next((item for item in self.tile_order if item not in self.hidden_tiles), "metrics")
        self._save()

    def _add_metric(self, key: str) -> None:
        pinned = self.config.setdefault("pinned", [])
        excluded = self.config.setdefault("excluded_metrics", [])
        if key in pinned:
            if key in excluded:
                excluded.remove(key)
            self._message("Metric is already on the dashboard")
        elif len(pinned) >= 32:
            self._message("Dashboard supports up to 32 pinned sensors")
            return
        else:
            if key in excluded:
                excluded.remove(key)
            pinned.append(key)
            self._message("Metric added to Live Metrics")
        self._save()

    def _remove_metric(self, key: str) -> None:
        pinned = self.config.setdefault("pinned", [])
        if key in pinned:
            pinned.remove(key)
        excluded = self.config.setdefault("excluded_metrics", [])
        if key not in excluded:
            excluded.append(key)
        self._save()
        self._message("Metric removed from Live Metrics")

    def _add_history_metric(self, key: str) -> None:
        keys = self.config.setdefault("history_keys", [])
        if key in keys:
            keys.remove(key)
        keys.insert(0, key)
        evicted = keys[8:]
        del keys[8:]
        if evicted:
            for group in self.config.setdefault("history_groups", []):
                if isinstance(group, dict) and isinstance(group.get("metrics"), list):
                    group["metrics"] = [member for member in group["metrics"] if member not in evicted]
        self.config["history_initialized"] = True
        self.selected_sensor = key
        self.tile_scroll["history"] = 0
        self._tile_focus("history")
        self._save()
        self._message("Metric added to Recent History")

    def _remove_history_metric(self, key: str) -> None:
        keys = self.config.setdefault("history_keys", [])
        if key in keys:
            keys.remove(key)
        for group in self.config.setdefault("history_groups", []):
            if isinstance(group, dict) and isinstance(group.get("metrics"), list) and key in group["metrics"]:
                group["metrics"].remove(key)
        if self.selected_sensor == key:
            self.selected_sensor = keys[0] if keys else None
        self._save()
        self._message("Metric removed from Recent History")

    def _history_groups(self) -> list[dict[str, Any]]:
        return self.config.setdefault("history_groups", [])

    def _active_history_group(self) -> str:
        name = self.config.get("active_history_group", "All")
        if name == "All" or any(group.get("name") == name for group in self._history_groups()):
            return name
        self.config["active_history_group"] = "All"
        return "All"

    def _active_history_keys(self) -> list[str]:
        keys = self.config.get("history_keys", [])
        name = self._active_history_group()
        if name == "All":
            return list(keys)
        group = next((item for item in self._history_groups() if item.get("name") == name), None)
        members = set(group.get("metrics", [])) if group else set()
        return [key for key in keys if key in members]

    def _cycle_history_group(self, delta: int) -> None:
        names = ["All"] + [group["name"] for group in self._history_groups()]
        current = self._active_history_group()
        index = names.index(current) if current in names else 0
        self.config["active_history_group"] = names[(index + delta) % len(names)]
        self.tile_scroll["recent"] = 0
        self._save()

    def _set_history_columns(self, delta: int) -> None:
        columns = min(6, max(1, int(self.config.get("history_columns", 3)) + delta))
        self.config["history_columns"] = columns
        self.tile_scroll["recent"] = 0
        self._save()
        self._message(f"Recent History: {columns} column{'s' if columns != 1 else ''}")

    def _history_chart_rows(self, key: str) -> int:
        values = self.config.setdefault("history_chart_rows", {})
        try:
            return min(MAX_HISTORY_CHART_ROWS, max(1, int(values.get(key, 1))))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return 1

    def _selected_history_metric_key(self) -> str | None:
        if self.view == "dashboard":
            if self.focused_tile != "history":
                return None
            keys = self.config.get("history_keys", [])
        elif self.view == "recent":
            keys = self._active_history_keys()
        else:
            return None
        return self.selected_sensor if self.selected_sensor in keys else (keys[0] if keys else None)

    def _history_chart_style(self, key: str) -> str:
        styles = self.config.setdefault("history_chart_styles", {})
        try:
            style = styles.get(key, "block")
        except AttributeError:
            return "block"
        return style if style in HISTORY_CHART_STYLES else "block"

    def _set_history_chart_rows(self, delta: int) -> None:
        key = self._selected_history_metric_key()
        if not key:
            self._message("Select a metric in Recent History first")
            return
        rows = min(MAX_HISTORY_CHART_ROWS, max(1, self._history_chart_rows(key) + delta))
        self.config.setdefault("history_chart_rows", {})[key] = rows
        self.selected_sensor = key
        self._save()
        records, _ = self.store.snapshot()
        label = records[key].reading.label if key in records else key
        self._message(f"{label}: chart height {rows} line{'s' if rows != 1 else ''}")

    def _cycle_history_chart_style(self) -> None:
        key = self._selected_history_metric_key()
        if not key:
            self._message("Select a metric in Recent History first")
            return
        current = self._history_chart_style(key)
        next_index = (HISTORY_CHART_STYLES.index(current) + 1) % len(HISTORY_CHART_STYLES)
        style = HISTORY_CHART_STYLES[next_index]
        self.config.setdefault("history_chart_styles", {})[key] = style
        self.selected_sensor = key
        self._save()
        records, _ = self.store.snapshot()
        label = records[key].reading.label if key in records else key
        self._message(f"{label}: {style.title()} chart style")

    def _toggle_history_averages(self) -> None:
        enabled = not bool(self.config.get("history_show_averages", False))
        self.config["history_show_averages"] = enabled
        self._save()
        self._message(f"Rolling history averages {'on' if enabled else 'off'}")

    def _open_history_group_manager(self) -> None:
        self.view = "recent"
        self.overlay = "history_groups"
        keys = self.config.get("history_keys", [])
        self.group_metric_index = keys.index(self.selected_sensor) if self.selected_sensor in keys else 0

    def _open_history_group_name(self, mode: str) -> None:
        active = self._active_history_group()
        if mode == "rename" and active == "All":
            self._message("Choose a named group before renaming it")
            return
        self.group_name_mode = mode
        self.overlay_query = active if mode == "rename" else ""
        self.overlay = "history_group_name"

    def _save_history_group_name(self) -> None:
        name = self.overlay_query.strip()[:32]
        groups = self._history_groups()
        mode = self.group_name_mode
        if not name or name.casefold() == "all":
            self._message("Choose a group name other than All")
            return
        if any(group.get("name", "").casefold() == name.casefold() and (mode != "rename" or group.get("name") != self._active_history_group()) for group in groups):
            self._message("A group with that name already exists")
            return
        if mode == "new":
            if len(groups) >= 32:
                self._message("History supports up to 32 groups")
                return
            groups.append({"name": name, "metrics": []})
            self.config["active_history_group"] = name
        elif mode == "rename":
            old_name = self._active_history_group()
            group = next((item for item in groups if item.get("name") == old_name), None)
            if group is None:
                self._message("The selected group no longer exists")
                return
            group["name"] = name
            self.config["active_history_group"] = name
        self.group_name_mode = None
        self.overlay = "history_groups"
        self.tile_scroll["recent"] = 0
        self._save()
        self._message(f"History group saved: {name}")

    def _request_history_group_delete(self) -> None:
        active = self._active_history_group()
        if active == "All":
            self._message("The All Metrics view cannot be deleted")
            return
        self.group_delete_name = active
        self.overlay = "history_group_delete"

    def _finish_history_group_delete(self) -> None:
        name = self.group_delete_name
        if name:
            self.config["history_groups"] = [group for group in self._history_groups() if group.get("name") != name]
            if self.config.get("active_history_group") == name:
                self.config["active_history_group"] = "All"
            self._save()
            self._message(f"History group deleted: {name}")
        self.group_delete_name = None
        self.overlay = "history_groups"

    def _toggle_history_group_metric(self, key: str | None = None) -> None:
        active = self._active_history_group()
        if active == "All":
            self._message("Create or select a group before changing its metrics")
            return
        keys = self.config.get("history_keys", [])
        if key is None:
            if not keys:
                self._message("Add a metric to Recent History first")
                return
            self.group_metric_index = min(max(0, self.group_metric_index), len(keys) - 1)
            key = keys[self.group_metric_index]
        group = next((item for item in self._history_groups() if item.get("name") == active), None)
        if group is None:
            self._message("The selected group no longer exists")
            return
        members = group.setdefault("metrics", [])
        if key in members:
            members.remove(key)
            message = "Metric removed from group"
        else:
            members.append(key)
            message = "Metric added to group"
        self.selected_sensor = key
        self._save()
        self._message(message)

    def _open_watch_form(self) -> None:
        records, _ = self.store.snapshot()
        record = records.get(self.selected_sensor) if self.selected_sensor else None
        if record is None:
            self._message("Choose a live sensor before adding a watch")
            return
        current = record.value if record.value is not None else 0.0
        if record.reading.kind == "temperature" or "°c" in record.reading.unit.lower() or record.reading.unit.lower() == "c":
            above = 85.0
        elif record.reading.unit == "%":
            above = min(100.0, current + max(5.0, (100.0 - current) * 0.2))
        else:
            above = current + max(1.0, abs(current) * 0.15)
        below = above - max(2.0, abs(above) * 0.05)
        self.watch_form = {
            "sensor": self.selected_sensor,
            "label": record.reading.label,
            "unit": record.reading.unit,
            "fields": [f"{above:.2f}", f"{below:.2f}", "0"],
        }
        self.watch_form_field = 0
        self.overlay = "watch_form"

    def _save_watch_form(self) -> None:
        if not self.watch_form:
            return
        try:
            above, below, duration = (float(value) for value in self.watch_form["fields"])
            if not all(math.isfinite(value) for value in (above, below, duration)):
                raise ValueError
            if below > above or duration < 0 or duration > 3600:
                raise ValueError
        except (ValueError, TypeError):
            self._message("Check the watch values: below ≤ above and duration 0–3600 seconds")
            return
        watches = self.config.setdefault("watches", [])
        if len(watches) >= 256:
            self._message("Watch limit reached")
            return
        watch_id = f"watch-{int(time.time() * 1000) % 1_000_000_000}"
        watches.append({
            "id": watch_id,
            "sensor": self.watch_form["sensor"],
            "label": self.watch_form["label"],
            "above": above,
            "below": below,
            "duration": duration,
            "active": False,
            "since": None,
        })
        self.watch_index = len(watches) - 1
        self.overlay = None
        self.watch_form = None
        self._save()
        self._message("Threshold watch added")

    def _remove_watch(self) -> None:
        watches = self.config.get("watches", [])
        if 0 <= self.watch_delete_index < len(watches):
            removed = watches.pop(self.watch_delete_index)
            self.watch_index = max(0, min(self.watch_delete_index, len(watches) - 1))
            self._save()
            self._message(f"Removed watch: {removed.get('label', removed.get('id', 'watch'))}")
        self.overlay = None

    def _open_theme_editor(self) -> None:
        self.view = "theme"
        self.theme_before_edit = copy.deepcopy(self.theme_custom)
        if isinstance(self.theme_custom, dict) and self.theme_custom.get("theme") == self.theme_name:
            self.theme_working = copy.deepcopy(self.theme_custom)
        else:
            self.theme_working = theme_document(self.theme)
        self.theme_anchor_index = min(self.theme_anchor_index, max(0, len(self.theme_working.get("palette", {}).get("anchors", [])) - 1))
        self.theme_editing = True
        self._new_theme_preview()
        self._message("Palette preview: arrows choose/adjust; Enter commits, Esc cancels", 6)

    def _new_theme_preview(self) -> None:
        if not self.theme_working:
            return
        self.theme = make_theme(self.theme_name, self.theme_working, self.colour_mode)
        self.previous_frame = None

    def _edit_theme(self, command_id: str) -> None:
        if not self.theme_working:
            return
        palette = self.theme_working.setdefault("palette", {})
        anchors = palette.get("anchors", [])
        if not anchors and command_id != "theme.anchor_add":
            return
        self.theme_anchor_index = min(max(0, self.theme_anchor_index), max(0, len(anchors) - 1))
        if command_id == "theme.anchor_add":
            if len(anchors) >= 32:
                self._message("Palette anchor limit reached")
                return
            sorted_anchors = sorted(anchors, key=lambda item: float(item.get("position", 0)))
            gaps = []
            for left, right in zip(sorted_anchors, sorted_anchors[1:]):
                gap = float(right["position"]) - float(left["position"])
                if gap >= 0.02:
                    gaps.append((gap, (float(left["position"]) + float(right["position"])) / 2))
            if not gaps:
                self._message("No room for another palette stop")
                return
            _, position = max(gaps)
            colour = self.theme.lut[round(position * (len(self.theme.lut) - 1))]
            anchors.append({"position": position, "colour": colour, "locked": False, "label": "new stop"})
            anchors.sort(key=lambda item: float(item["position"]))
            self.theme_anchor_index = next(i for i, item in enumerate(anchors) if item["position"] == position)
        elif command_id == "theme.anchor_remove":
            anchor = anchors[self.theme_anchor_index]
            if anchor.get("locked"):
                self._message("Unlock this palette stop before removing it")
                return
            if len(anchors) <= 2:
                self._message("A LUT needs at least two anchors")
                return
            anchors.pop(self.theme_anchor_index)
            self.theme_anchor_index = min(self.theme_anchor_index, len(anchors) - 1)
        elif command_id == "theme.anchor_lock":
            anchor = anchors[self.theme_anchor_index]
            anchor["locked"] = not bool(anchor.get("locked"))
        elif command_id in {"theme.anchor_position_left", "theme.anchor_position_right"}:
            anchor = anchors[self.theme_anchor_index]
            if anchor.get("locked"):
                self._message("Unlock this palette stop before moving it")
                return
            ordered = sorted(anchors, key=lambda item: float(item.get("position", 0)))
            index = ordered.index(anchor)
            lower = float(ordered[index - 1]["position"]) + 0.01 if index else 0.0
            upper = float(ordered[index + 1]["position"]) - 0.01 if index + 1 < len(ordered) else 1.0
            delta = -0.01 if command_id.endswith("left") else 0.01
            anchor["position"] = round(min(upper, max(lower, float(anchor["position"]) + delta)), 3)
            anchors.sort(key=lambda item: float(item["position"]))
            self.theme_anchor_index = anchors.index(anchor)
        elif command_id == "theme.anchor_edit":
            self.theme_colour_draft = anchors[self.theme_anchor_index].get("colour", "#")
            self.overlay = "theme_hex"
            return
        elif command_id == "theme.seed_next":
            palette["seed"] = int(palette.get("seed", 0)) + 1
        elif command_id == "theme.anchor_prev":
            self.theme_anchor_index = (self.theme_anchor_index - 1) % len(anchors)
        elif command_id == "theme.anchor_next":
            self.theme_anchor_index = (self.theme_anchor_index + 1) % len(anchors)
        elif command_id == "theme.hue_down":
            self.theme_working["palette"] = shift_anchor_hue(palette, self.theme_anchor_index, -0.015)
        elif command_id == "theme.hue_up":
            self.theme_working["palette"] = shift_anchor_hue(palette, self.theme_anchor_index, 0.015)
        elif command_id == "theme.cycle":
            palette["cyclic"] = not bool(palette.get("cyclic", False))
        elif command_id == "theme.interpolation":
            palette["interpolation"] = "linear" if palette.get("interpolation", "srgb") == "srgb" else "srgb"
        elif command_id == "theme.variation_down":
            palette["variation"] = max(0, int(palette.get("variation", 0)) - 5)
        elif command_id == "theme.variation_up":
            palette["variation"] = min(100, int(palette.get("variation", 0)) + 5)
        elif command_id == "theme.commit":
            self.theme_custom = copy.deepcopy(self.theme_working)
            self.theme_editing = False
            self.theme_before_edit = None
            self.theme_working = None
            self._new_theme()
            self._save()
            self._message("Palette committed")
            return
        elif command_id == "theme.cancel":
            self.theme_custom = copy.deepcopy(self.theme_before_edit or {})
            self.theme_editing = False
            self.theme_before_edit = None
            self.theme_working = None
            self._new_theme()
            self._message("Palette preview discarded")
            return
        self._new_theme_preview()

    def _commit_theme_hex(self) -> None:
        if not self.theme_working:
            self.overlay = None
            return
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.theme_colour_draft):
            self._message("Colour must use #RRGGBB")
            return
        anchors = self.theme_working.get("palette", {}).get("anchors", [])
        if not anchors:
            self.overlay = None
            return
        anchors[self.theme_anchor_index]["colour"] = self.theme_colour_draft.lower()
        self.overlay = None
        self._new_theme_preview()

    def _check_watches(self, records: dict[str, SensorRecord], status: SourceStatus) -> None:
        sample_count = status.sample_count
        if sample_count == self._sample_count:
            return
        self._sample_count = sample_count
        now = time.time()
        changed = False
        for watch in self.config.get("watches", []):
            record = records.get(watch.get("sensor"))
            if record is None or self.store.quality(record, now) != "good" or record.value is None:
                if watch.get("since") is not None and not watch.get("active"):
                    watch["since"] = None
                    changed = True
                continue
            value = record.value
            if value >= float(watch["above"]):
                if watch.get("since") is None:
                    watch["since"] = now
                    changed = True
                duration = max(0.0, float(watch.get("duration", 0)))
                if not watch.get("active") and now - float(watch["since"]) >= duration:
                    watch["active"] = True
                    changed = True
                    self._add_event(watch, "triggered", value)
            elif value <= float(watch["below"]):
                if watch.get("active"):
                    self._add_event(watch, "cleared", value)
                    watch["active"] = False
                    changed = True
                if watch.get("since") is not None:
                    watch["since"] = None
                    changed = True
        if changed:
            self._save()

    def _add_event(self, watch: dict[str, Any], state: str, value: float) -> None:
        events = self.config.setdefault("events", [])
        events.insert(0, {
            "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "watch": watch.get("id", ""),
            "sensor": watch.get("sensor", ""),
            "label": watch.get("label", watch.get("sensor", "")),
            "state": state,
            "value": value,
        })
        del events[100:]
        self._message(f"{watch.get('label', 'Sensor')} watch {state}: {value:g}", 6)

    def _cancel_drag(self) -> None:
        if not self.drag:
            return
        if self.drag["axis"] == "recent_column":
            self.config["history_column_widths"] = copy.deepcopy(self.drag["before_widths_setting"])
        elif self.drag["axis"] == "recent_chart_split":
            key = self.drag["key"]
            widths = self.config.setdefault("history_stats_widths", {})
            if self.drag["before_stats_width"] is None:
                widths.pop(key, None)
            else:
                widths[key] = self.drag["before_stats_width"]
        elif self.drag["axis"] == "recent_card":
            key = self.drag["key"]
            rows = self.config.setdefault("history_chart_rows", {})
            if self.drag["before_chart_rows"] is None:
                rows.pop(key, None)
            else:
                rows[key] = self.drag["before_chart_rows"]
        else:
            self.column_ratio = self.drag["before_column"]
            self.row_ratio = self.drag["before_row"]
        self.drag = None

    def _handle_event(self, event: Event) -> None:
        if event.kind == "focus" and event.action == "out":
            if self.drag:
                self._cancel_drag()
            return
        if event.kind == "mouse":
            self._handle_mouse(event)
            return
        if event.kind == "text":
            self._handle_text(event.text)
            return
        if event.kind == "key":
            self._handle_key(event.key, event)

    def _handle_text(self, value: str) -> None:
        if self.overlay == "palette":
            self.overlay_query += value
            self.overlay_index = 0
        elif self.overlay == "filter":
            self.overlay_query += value
        elif self.overlay == "history_group_name":
            if len(self.overlay_query) < 32 and value.isprintable():
                self.overlay_query += value
        elif self.overlay == "history_groups":
            if value.lower() == "n":
                self._open_history_group_name("new")
            elif value.lower() == "r":
                self._open_history_group_name("rename")
            elif value.lower() == "d":
                self._request_history_group_delete()
            elif value.lower() == "g":
                self.overlay = None
            elif value == " ":
                self._toggle_history_group_metric()
            return
        elif self.overlay == "history_group_delete":
            if value.lower() == "y":
                self._finish_history_group_delete()
            elif value.lower() == "n":
                self.group_delete_name = None
                self.overlay = "history_groups"
            return
        elif self.overlay == "watch_form" and self.watch_form:
            fields = self.watch_form["fields"]
            current = fields[self.watch_form_field]
            if value.isdigit() or (value in ".-" and value not in current):
                fields[self.watch_form_field] = current + value
        elif self.overlay == "theme_hex":
            if value in "0123456789abcdefABCDEF" and len(self.theme_colour_draft) < 7:
                self.theme_colour_draft += value
        elif self.overlay == "watch_delete":
            return
        elif self.theme_editing:
            if value.lower() == "c":
                self._dispatch("theme.cycle")
            elif value.lower() == "i":
                self._dispatch("theme.interpolation")
            elif value == "+":
                self._dispatch("theme.variation_up")
            elif value == "-":
                self._dispatch("theme.variation_down")
            elif value.lower() == "a":
                self._dispatch("theme.anchor_add")
            elif value.lower() == "x":
                self._dispatch("theme.anchor_remove")
            elif value.lower() == "l":
                self._dispatch("theme.anchor_lock")
            elif value.lower() == "h":
                self._dispatch("theme.anchor_edit")
            elif value.lower() == "s":
                self._dispatch("theme.seed_next")
            elif value == ",":
                self._dispatch("theme.anchor_position_left")
            elif value == ".":
                self._dispatch("theme.anchor_position_right")
        elif value == "/":
            self._dispatch("sensor.filter")
        elif value == "?":
            self._dispatch("view.help")
        elif self.view == "recent" and value in {"[", "]"}:
            self._dispatch("history.columns_less" if value == "[" else "history.columns_more")
        else:
            self._dispatch_by_key(value.lower())

    def _handle_key(self, key: str, event: Event) -> None:
        if self.overlay == "palette":
            if key == "escape" or key == "ctrl+p":
                self.overlay = None
                self.overlay_query = ""
            elif key == "up":
                self.overlay_index = max(0, self.overlay_index - 1)
            elif key == "down":
                self.overlay_index = min(max(0, len(self._filtered_commands()) - 1), self.overlay_index + 1)
            elif key == "enter":
                rows = self._filtered_commands()
                if rows:
                    self._dispatch(rows[self.overlay_index % len(rows)].id)
            elif key == "backspace":
                self.overlay_query = self.overlay_query[:-1]
                self.overlay_index = 0
            elif key == "ctrl+a":
                self.overlay_query = ""
                self.overlay_index = 0
            return
        if self.overlay == "filter":
            if key == "escape":
                self.overlay = None
                self.overlay_query = ""
            elif key == "enter":
                self.filter_text = self.overlay_query.strip()
                self.tile_scroll["sensors"] = 0
                self.overlay = None
                self._save()
            elif key == "backspace":
                self.overlay_query = self.overlay_query[:-1]
            elif key == "ctrl+a":
                self.overlay_query = ""
            return
        if self.overlay == "watch_form":
            if key == "escape":
                self.overlay = None
                self.watch_form = None
            elif key == "tab" or key == "enter":
                if self.watch_form_field < 2:
                    self.watch_form_field += 1
                else:
                    self._save_watch_form()
            elif key == "backspace" and self.watch_form:
                fields = self.watch_form["fields"]
                fields[self.watch_form_field] = fields[self.watch_form_field][:-1]
            elif key == "ctrl+a" and self.watch_form:
                self.watch_form["fields"][self.watch_form_field] = ""
            elif key == "left" and self.watch_form:
                self.watch_form_field = max(0, self.watch_form_field - 1)
            elif key == "right" and self.watch_form:
                self.watch_form_field = min(2, self.watch_form_field + 1)
            elif key == "ctrl+s":
                self._save_watch_form()
            return
        if self.overlay == "theme_hex":
            if key == "escape":
                self.overlay = None
            elif key == "enter":
                self._commit_theme_hex()
            elif key == "backspace":
                if len(self.theme_colour_draft) > 1:
                    self.theme_colour_draft = self.theme_colour_draft[:-1]
            elif key == "ctrl+a":
                self.theme_colour_draft = "#"
            return
        if self.overlay == "history_group_name":
            if key == "escape":
                self.overlay = "history_groups"
                self.group_name_mode = None
            elif key == "enter":
                self._save_history_group_name()
            elif key == "backspace":
                self.overlay_query = self.overlay_query[:-1]
            elif key == "ctrl+a":
                self.overlay_query = ""
            return
        if self.overlay == "history_group_delete":
            if key in {"escape", "n"}:
                self.group_delete_name = None
                self.overlay = "history_groups"
            elif key in {"enter", "y"}:
                self._finish_history_group_delete()
            return
        if self.overlay == "history_groups":
            keys = self.config.get("history_keys", [])
            if key == "escape":
                self.overlay = None
            elif key == "up" and keys:
                self.group_metric_index = max(0, self.group_metric_index - 1)
            elif key == "down" and keys:
                self.group_metric_index = min(len(keys) - 1, self.group_metric_index + 1)
            elif key == "left":
                self._cycle_history_group(-1)
            elif key == "right":
                self._cycle_history_group(1)
            elif key == "enter":
                self._toggle_history_group_metric()
            return
        if self.overlay == "watch_delete":
            if key in {"escape", "n"}:
                self.overlay = None
            elif key in {"enter", "y"}:
                self._remove_watch()
            elif key in {"left", "right"}:
                self.watch_delete_index = min(max(0, self.watch_delete_index), max(0, len(self.config.get("watches", [])) - 1))
            return
        if self.theme_editing:
            if key == "escape":
                self._dispatch("theme.cancel")
            elif key == "enter":
                self._dispatch("theme.commit")
            elif key == "left":
                self._dispatch("theme.anchor_prev")
            elif key == "right":
                self._dispatch("theme.anchor_next")
            elif key == "up":
                self._dispatch("theme.hue_up")
            elif key == "down":
                self._dispatch("theme.hue_down")
            elif key == "ctrl+p":
                self._dispatch("command_palette.open")
            elif key == "backspace":
                return
            return

        if self.view == "recent" and key in {"left", "right"}:
            self._cycle_history_group(-1 if key == "left" else 1)
            return
        if self.view == "recent" and key in {"up", "down"}:
            self._move_recent_selection(-1 if key == "up" else 1)
            return

        if key == "escape":
            if self.drag:
                self._cancel_drag()
                return
            if self.maximized_tile:
                self.maximized_tile = None
                self._save()
            return
        if key == "up" or key == "down":
            self._move_selection(-1 if key == "up" else 1)
            return
        if key == "pageup" or key == "pagedown":
            self._scroll(-1 if key == "pageup" else 1, page=True)
            return
        if key == "enter" and self.selected_sensor:
            self._dispatch("view.history")
            return
        self._dispatch_by_key(key)

    def _dispatch_by_key(self, key: str) -> None:
        for command in COMMANDS:
            if key in command.keys:
                if command.id.startswith(("history.chart_rows_", "history.chart_style_")):
                    if not (
                        self.view == "recent"
                        or (self.view == "dashboard" and self.focused_tile == "history")
                    ):
                        continue
                elif command.id.startswith("history.stats_averages_"):
                    if not (
                        self.view in {"recent", "history"}
                        or (self.view == "dashboard" and self.focused_tile == "history")
                    ):
                        continue
                elif command.id.startswith("history.") and self.view != "recent":
                    continue
                if self.view == "recent" and command.id.startswith(("layout.", "tile.")):
                    continue
                editor_commands = {
                    "theme.cycle", "theme.interpolation", "theme.anchor_prev", "theme.anchor_next",
                    "theme.hue_down", "theme.hue_up", "theme.variation_down", "theme.variation_up",
                    "theme.commit", "theme.cancel",
                }
                if command.id in editor_commands and not self.theme_editing:
                    continue
                self._dispatch(command.id)
                return

    def _move_selection(self, delta: int) -> None:
        records, _ = self.store.snapshot()
        ordered = self._ordered_records(records)
        if self.view == "alerts" and self.config.get("watches"):
            self.watch_index = min(max(0, self.watch_index + delta), len(self.config["watches"]) - 1)
        elif ordered:
            if self.selected_sensor not in [record.reading.key for record in ordered]:
                self.selected_sensor_index = 0
            else:
                self.selected_sensor_index = [record.reading.key for record in ordered].index(self.selected_sensor)
            self.selected_sensor_index = min(max(0, self.selected_sensor_index + delta), len(ordered) - 1)
            self.selected_sensor = ordered[self.selected_sensor_index].reading.key
            self._tile_focus("sensors" if self.view == "sensors" else "history")

    def _move_recent_selection(self, delta: int) -> None:
        keys = self._active_history_keys()
        if not keys:
            return
        index = keys.index(self.selected_sensor) if self.selected_sensor in keys else 0
        index = min(max(0, index + delta), len(keys) - 1)
        self.selected_sensor = keys[index]

    def _scroll(self, delta: int, page: bool = False, tile: str | None = None) -> None:
        target = tile or (self.focused_tile if self.view == "dashboard" else self.view)
        amount = 6 if page else 3
        self.tile_scroll[target] = max(0, self.tile_scroll.get(target, 0) + delta * amount)

    def _handle_mouse(self, event: Event) -> None:
        if event.action == "move" and self.drag:
            width, height = shutil.get_terminal_size((120, 40))
            if self.drag["axis"] == "column":
                available = max(1, width - 3)
                self.column_ratio = min(0.78, max(0.22, (event.x - 1) / available))
            elif self.drag["axis"] == "row":
                body_height = max(1, height - 6)
                self.row_ratio = min(0.78, max(0.22, (event.y - 3) / body_height))
            elif self.drag["axis"] == "recent_column":
                widths = self.drag["before_widths"].copy()
                column = self.drag["column"]
                pair_width = widths[column] + widths[column + 1]
                left_width = widths[column] + event.x - self.drag["start_x"]
                left_width = min(pair_width - 24, max(24, left_width))
                widths[column] = left_width
                widths[column + 1] = pair_width - left_width
                self._set_recent_column_widths(widths)
            elif self.drag["axis"] == "recent_card":
                key = self.drag["key"]
                rows = min(
                    MAX_HISTORY_CHART_ROWS,
                    max(1, self.drag["start_rows"] + event.y - self.drag["start_y"]),
                )
                self.config.setdefault("history_chart_rows", {})[key] = rows
            elif self.drag["axis"] == "recent_chart_split":
                key = self.drag["key"]
                stats_width = min(
                    self.drag["max_stats_width"],
                    max(7, self.drag["start_stats_width"] - event.x + self.drag["start_x"]),
                )
                self.config.setdefault("history_stats_widths", {})[key] = stats_width
            return
        if event.action == "release" and self.drag:
            drag = self.drag
            self.drag = None
            self._save()
            if drag["axis"] == "recent_column":
                self._message("Recent History column widths updated")
            elif drag["axis"] == "recent_card":
                self._message(f"History chart height: {self.config['history_chart_rows'][drag['key']]} rows")
            elif drag["axis"] == "recent_chart_split":
                widths = self.config.setdefault("history_stats_widths", {})
                self._message(f"Chart/readout split: {widths.get(drag['key'], drag['start_stats_width'])} cells")
            return
        if event.action == "wheel":
            if self.overlay:
                if self.overlay == "palette":
                    command_count = len(self._filtered_commands())
                    self.overlay_index = min(
                        max(0, command_count - 1),
                        max(0, self.overlay_index - event.wheel),
                    )
                return
            tile_hit = next((region for region in reversed(self.hits) if region.action == "tile_focus" and region.contains(event.x, event.y)), None)
            tile = tile_hit.payload if tile_hit else None
            self._scroll(-event.wheel, tile=tile)
            return
        if event.action == "press" and event.button == 2:
            self.last_click = None
            if self.overlay:
                self.last_right_click = None
                return
            hit = self._hit(event.x, event.y)
            if hit and hit.action in {"metric_select", "history_select", "recent_metric_select"}:
                now = time.monotonic()
                target = (hit.action, str(hit.payload))
                is_double = bool(self.last_right_click and now - self.last_right_click[0] <= 0.5 and self.last_right_click[1:] == target)
                self.last_right_click = None if is_double else (now, *target)
                if is_double:
                    if hit.action == "metric_select":
                        self._remove_metric(str(hit.payload))
                    else:
                        self._remove_history_metric(str(hit.payload))
                else:
                    self._message("Right-click the same metric again to remove it")
            else:
                self.last_right_click = None
            return
        if event.action != "press" or event.button != 0:
            return
        self.last_right_click = None
        hit = self._hit(event.x, event.y)
        if self.overlay:
            self.last_click = None
            overlay_actions = {
                "overlay_surface", "palette_close", "palette_command", "filter_apply", "watch_field",
                "watch_save", "watch_cancel", "watch_delete_confirm", "watch_delete_cancel",
                "theme_hex_save", "theme_hex_cancel", "history_group_cycle", "history_group_action",
                "history_group_metric", "history_group_name_save", "history_group_name_cancel",
                "history_group_delete_confirm", "history_group_delete_cancel",
            }
            if hit and hit.action in overlay_actions:
                self._activate_hit(hit)
            else:
                # Consume an outside click while dismissing the top overlay.
                self.overlay = None
                self.watch_form = None
            return
        if hit and hit.action == "recent_column_resize":
            payload = hit.payload if isinstance(hit.payload, dict) else {}
            self.last_click = None
            self.drag = {
                "axis": "recent_column",
                "column": int(payload.get("column", 0)),
                "before_widths": list(payload.get("widths", [])),
                "before_widths_setting": copy.deepcopy(self.config.get("history_column_widths", [])),
                "start_x": event.x,
            }
            return
        if hit and hit.action == "recent_card_resize":
            key = str(hit.payload)
            chart_rows = self.config.setdefault("history_chart_rows", {})
            self.last_click = None
            self.drag = {
                "axis": "recent_card",
                "key": key,
                "start_y": event.y,
                "start_rows": self._history_chart_rows(key),
                "before_chart_rows": chart_rows.get(key),
            }
            return
        if hit and hit.action == "recent_chart_split":
            payload = hit.payload if isinstance(hit.payload, dict) else {}
            key = str(payload.get("key", ""))
            stats_widths = self.config.setdefault("history_stats_widths", {})
            self.last_click = None
            self.drag = {
                "axis": "recent_chart_split",
                "key": key,
                "start_x": event.x,
                "start_stats_width": int(payload.get("stats_width", 7)),
                "max_stats_width": int(payload.get("max_stats_width", 7)),
                "before_stats_width": stats_widths.get(key),
            }
            return
        if hit and hit.action in {"column_divider", "row_divider"}:
            self.last_click = None
            self.drag = {
                "axis": "column" if hit.action == "column_divider" else "row",
                "before_column": self.column_ratio,
                "before_row": self.row_ratio,
            }
            return
        if hit and hit.action in {"sensor_select", "metric_select"}:
            now = time.monotonic()
            is_double = bool(
                self.last_click
                and now - self.last_click[0] <= 0.5
                and self.last_click[1:] == (hit.action, hit.payload)
            )
            self.last_click = None if is_double else (now, hit.action, hit.payload)
            self._activate_hit(hit)
            if is_double:
                if hit.action == "sensor_select":
                    self._add_metric(str(hit.payload))
                else:
                    self._add_history_metric(str(hit.payload))
            return
        self.last_click = None
        if hit:
            self._activate_hit(hit)

    def _hit(self, x: int, y: int) -> HitRegion | None:
        for region in reversed(self.hits):
            if region.contains(x, y):
                return region
        return None

    def _activate_hit(self, hit: HitRegion) -> None:
        if hit.action == "command":
            self._dispatch(str(hit.payload))
        elif hit.action == "tile_hide":
            self._hide_tile(str(hit.payload))
        elif hit.action == "tile_maximize":
            self._maximize_tile(str(hit.payload))
        elif hit.action == "tile_focus":
            self._tile_focus(str(hit.payload))
        elif hit.action == "sensor_select":
            self.selected_sensor = str(hit.payload)
            records, _ = self.store.snapshot()
            ordered_keys = [record.reading.key for record in self._ordered_records(records)]
            if self.selected_sensor in ordered_keys:
                self.selected_sensor_index = ordered_keys.index(self.selected_sensor)
            self._tile_focus("sensors" if self.view in {"dashboard", "sensors"} else "history")
        elif hit.action == "metric_select":
            self.selected_sensor = str(hit.payload)
            self._tile_focus("metrics")
        elif hit.action == "history_select":
            self.selected_sensor = str(hit.payload)
            self._tile_focus("history")
        elif hit.action == "recent_metric_select":
            self.selected_sensor = str(hit.payload)
        elif hit.action == "history_group_cycle":
            self._cycle_history_group(int(hit.payload))
        elif hit.action == "history_group_action":
            action = str(hit.payload)
            if action == "new":
                self._open_history_group_name("new")
            elif action == "rename":
                self._open_history_group_name("rename")
            elif action == "delete":
                self._request_history_group_delete()
            elif action == "done":
                self.overlay = None
        elif hit.action == "history_group_metric":
            self._toggle_history_group_metric(str(hit.payload))
            keys = self.config.get("history_keys", [])
            self.group_metric_index = keys.index(hit.payload) if hit.payload in keys else 0
        elif hit.action == "history_group_name_save":
            self._save_history_group_name()
        elif hit.action == "history_group_name_cancel":
            self.group_name_mode = None
            self.overlay = "history_groups"
        elif hit.action == "history_group_delete_confirm":
            self._finish_history_group_delete()
        elif hit.action == "history_group_delete_cancel":
            self.group_delete_name = None
            self.overlay = "history_groups"
        elif hit.action == "watch_select":
            self.watch_index = int(hit.payload)
            self.view = "alerts"
        elif hit.action == "palette_command":
            self._dispatch(str(hit.payload))
        elif hit.action == "palette_close":
            self.overlay = None
            self.overlay_query = ""
        elif hit.action == "filter_apply":
            self.filter_text = self.overlay_query.strip()
            self.tile_scroll["sensors"] = 0
            self.overlay = None
            self._save()
        elif hit.action == "watch_field":
            self.watch_form_field = int(hit.payload)
        elif hit.action == "watch_save":
            self._save_watch_form()
        elif hit.action == "watch_cancel":
            self.overlay = None
            self.watch_form = None
        elif hit.action == "watch_delete_confirm":
            self._remove_watch()
        elif hit.action == "watch_delete_cancel":
            self.overlay = None
        elif hit.action == "theme_hex_save":
            self._commit_theme_hex()
        elif hit.action == "theme_hex_cancel":
            self.overlay = None
        elif hit.action == "theme_anchor":
            self.theme_anchor_index = int(hit.payload)
        elif hit.action == "theme_select":
            if self.theme_editing:
                self._message("Commit or cancel the palette preview before changing themes")
                return
            self.theme_name = str(hit.payload)
            self.theme_custom = {}
            self.theme_editing = False
            self.theme_working = None
            self._new_theme()
            self._save()
        elif hit.action == "theme_commit":
            self._dispatch("theme.commit")
        elif hit.action == "theme_cancel":
            self._dispatch("theme.cancel")

    def run(self) -> int:
        self.reader.start()
        try:
            with TerminalSession(mouse=self.mouse_enabled) as terminal:
                while not self.quit_requested:
                    width, height = shutil.get_terminal_size((120, 40))
                    width = max(1, width)
                    height = max(1, height)
                    if self.previous_size != (width, height):
                        sys.stdout.write("\x1b[2J")
                        self.previous_frame = None
                        self.previous_size = (width, height)
                    records, status = self.store.snapshot()
                    self._check_watches(records, status)
                    canvas = Canvas(width, height, self.ascii_only)
                    self.hits = []
                    if width < 40 or height < 10:
                        self._draw_resize_prompt(canvas, width, height)
                    else:
                        self._draw_chrome(canvas, width, height, records, status)
                        if self.view == "dashboard":
                            self._draw_dashboard(canvas, width, height, records, status)
                        elif self.view == "sensors":
                            self._draw_sensors_page(canvas, width, height, records)
                        elif self.view == "history":
                            self._draw_history_page(canvas, width, height, records)
                        elif self.view == "recent":
                            self._draw_recent_page(canvas, width, height, records)
                        elif self.view == "alerts":
                            self._draw_alerts_page(canvas, width, height, records)
                        elif self.view == "theme":
                            self._draw_theme_page(canvas, width, height)
                        elif self.view == "source":
                            self._draw_source_page(canvas, width, height, records, status)
                        else:
                            self._draw_help_page(canvas, width, height)
                        self._draw_footer(canvas, width, height, records, status)
                        if self.overlay:
                            self._draw_overlay(canvas, width, height, records)
                    output, self.previous_frame = canvas.render(self.theme, self.previous_frame)
                    if output:
                        sys.stdout.write(output)
                        sys.stdout.flush()
                    event = terminal.next_event(0.08)
                    if event:
                        self._handle_event(event)
        except KeyboardInterrupt:
            self.quit_requested = True
        finally:
            self.reader.stop()
            if self.theme_editing:
                self._dispatch("theme.cancel")
            if not self.config_error:
                self._save()
        return 0

    def _register(self, x: int, y: int, width: int, height: int, action: str, payload: Any = None) -> None:
        if width > 0 and height > 0:
            self.hits.append(HitRegion(x, y, width, height, action, payload))

    def _draw_resize_prompt(self, canvas: Canvas, width: int, height: int) -> None:
        message = "Resize Windows Terminal to at least 40 × 10 cells"
        canvas.put(max(0, (width - len(message)) // 2), max(0, height // 2), message, "status.warning", "surface.base", True)

    def _draw_chrome(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord], status: SourceStatus) -> None:
        canvas.fill(0, 0, width, " ", "text.primary", "surface.base")
        title = "HWiNFOterm"
        marker = "  SIMULATED DATA" if self.demo else "  HWiNFO SM2"
        if width >= 60 and not self.demo:
            marker = "  WINDOWS TERMINAL · HWiNFO SM2"
        canvas.put(1, 0, title, "accent.primary", "surface.base", True)
        canvas.put(1 + len(title), 0, marker, "text.secondary", "surface.base")
        clock = dt.datetime.now().strftime("%H:%M:%S")
        canvas.put(max(0, width - len(clock) - 2), 0, clock, "text.secondary", "surface.base")

        tabs = [
            ("Dashboard", "view.dashboard", "dashboard"),
            ("Sensors", "view.sensors", "sensors"),
            ("History", "view.history", "history"),
            ("Recent", "view.recent", "recent"),
            ("Alerts", "view.alerts", "alerts"),
            ("Theme", "view.theme", "theme"),
            ("Source", "view.source", "source"),
            ("Help", "view.help", "help"),
        ]
        if width < 112:
            tabs = [(short, command, view) for short, (_, command, view) in zip(("D", "S", "H", "N", "A", "T", "R", "?"), tabs)]
        x = 1
        for label, command, view in tabs:
            text = f" {label} "
            active = self.view == view
            canvas.put(x, 1, text, "text.inverse" if active else "text.secondary", "accent.primary" if active else "surface.raised", active)
            self._register(x, 1, len(text), 1, "command", command)
            x += len(text) + 1
        cmd_label = "[C]" if width < 112 else "[ Commands · Ctrl+P ]"
        canvas.put(max(x + 1, width - len(cmd_label) - 2), 1, cmd_label, "accent.secondary", "surface.base", True)
        self._register(max(x + 1, width - len(cmd_label) - 2), 1, len(cmd_label), 1, "command", "command_palette.open")

        state_colour = "status.success" if status.state in {"connected", "demo"} else "status.warning" if status.state in {"stale", "connecting"} else "status.error"
        sensor_count = len(records)
        age = "—"
        if status.last_change is not None:
            age = f"{max(0.0, time.time() - status.last_change):.1f}s ago"
        source_text = f"{status.state.upper()}  ·  {sensor_count} readings  ·  last change {age}  ·  {status.message}"
        canvas.fill(0, 2, width, " ", "text.secondary", "surface.raised")
        canvas.put(1, 2, source_text, state_colour, "surface.raised", status.state in {"stale", "unavailable"})

    def _draw_footer(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord], status: SourceStatus) -> None:
        if height < 2:
            return
        status_y = height - 2
        hint_y = height - 1
        canvas.fill(0, status_y, width, " ", "text.secondary", "surface.raised")
        if time.monotonic() < self.status_deadline and self.status_message:
            footer = self.status_message
            role = "status.warning" if "could not" in footer.lower() or "error" in footer.lower() else "accent.primary"
        elif self.config_error:
            footer, role = self.config_error, "status.warning"
        else:
            footer = "Read-only HWiNFO reader" if not self.demo else "Synthetic demo values · never presented as hardware readings"
            role = "text.secondary"
        canvas.put(1, status_y, footer, role, "surface.raised", role != "text.secondary")

        if self.view == "dashboard":
            hints = "Tab focus   M maximize   H hide   R restore   [ ] / - = resize   Ctrl+P commands   Q quit"
        elif self.view == "sensors":
            hints = "↑↓ select   / filter   P pin   Enter history   W watch   Ctrl+P commands   Q quit"
        elif self.view == "history":
            hints = "↑↓ change sensor   2 all sensors   1 dashboard   Ctrl+P commands   Q quit"
        elif self.view == "recent":
            hints = "←→ group   ↑↓ select metric   [ ] columns   G manage groups   wheel scroll   Q quit"
        elif self.view == "alerts":
            hints = "↑↓ select watch   W add   X remove   1 dashboard   Ctrl+P commands   Q quit"
        elif self.view == "theme":
            hints = "← → select   ↑ ↓ hue   A/X add/remove   H hex   Enter commit   Esc cancel   Ctrl+P commands" if self.theme_editing else "T next theme   E palette composer   Ctrl+P commands   Q quit"
        elif self.view == "source":
            hints = "1 dashboard   2 sensors   3 history   7 recent   4 alerts   5 themes   Ctrl+P commands   Q quit"
        else:
            hints = "1–7 navigate   ? help   Ctrl+P commands   Q quit"
        canvas.fill(0, hint_y, width, " ", "text.secondary", "surface.base")
        canvas.put(1, hint_y, hints, "text.secondary", "surface.base", max_width=width - 2)

    def _draw_dashboard(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord], status: SourceStatus) -> None:
        top = 3
        footer = height - 2
        available_height = max(1, footer - top)
        visible = self._visible_tiles()
        if not visible:
            visible = ["metrics"]
            self.hidden_tiles.discard("metrics")
        compact = width < 68 or available_height < 12
        if compact:
            current = self.maximized_tile or self.focused_tile
            if current not in visible:
                current = visible[0]
                self.focused_tile = current
            canvas.put(1, top, f"COMPACT · focused tile: {current} · {len(self.hidden_tiles)} hidden · Tab changes focus", "status.warning", "surface.base", True)
            if available_height > 2:
                self._draw_tile(canvas, current, 0, top + 1, width, available_height - 1, records, compact=True)
        elif self.maximized_tile in visible:
            self._draw_tile(canvas, self.maximized_tile, 0, top, width, available_height, records)
        elif len(visible) == 1:
            self._draw_tile(canvas, visible[0], 0, top, width, available_height, records)
        else:
            inner_width = max(1, width - 1)
            left = min(inner_width - 28, max(28, round(inner_width * self.column_ratio)))
            right = max(1, inner_width - left - 1)
            split_x = left
            inner_height = max(1, available_height)
            upper = min(inner_height - 6, max(6, round(inner_height * self.row_ratio)))
            lower = max(1, inner_height - upper - 1)
            split_y = top + upper
            slots = [
                (0, top, left, upper),
                (split_x + 1, top, right, upper),
                (0, split_y + 1, left, lower),
                (split_x + 1, split_y + 1, right, lower),
            ]
            for index, (x, y, w, h) in enumerate(slots):
                if index < len(visible):
                    self._draw_tile(canvas, visible[index], x, y, w, h, records)
                else:
                    self._draw_empty_tile(canvas, x, y, w, h)
            divider_x = split_x
            divider_y = split_y
            canvas.fill(divider_x, top, 1, "│" if not self.ascii_only else "|", "border.strong", "surface.base")
            canvas.fill(0, divider_y, width, "─" if not self.ascii_only else "-", "border.strong", "surface.base")
            canvas.put(divider_x, divider_y, "┼" if not self.ascii_only else "+", "accent.secondary", "surface.base", True)
            self._register(divider_x, top, 1, available_height, "column_divider")
            self._register(0, divider_y, width, 1, "row_divider")
        if self.hidden_tiles:
            restore = f"  {len(self.hidden_tiles)} tile(s) hidden · [restore all]"
            x = max(1, width - len(restore) - 1)
            canvas.put(x, top, restore, "status.warning", "surface.base")
            self._register(x, top, len(restore), 1, "command", "tile.restore_all")

    def _draw_empty_tile(self, canvas: Canvas, x: int, y: int, width: int, height: int) -> None:
        if width < 5 or height < 3:
            return
        canvas.rect(x, y, width, height, "border.normal", "surface.tile")
        label = "+ restore tile"
        canvas.put(x + 2, y + height // 2, label, "text.secondary", "surface.tile")
        self._register(x, y, width, height, "command", "tile.restore_all")

    def _draw_tile(self, canvas: Canvas, tile: str, x: int, y: int, width: int, height: int, records: dict[str, SensorRecord], compact: bool = False) -> None:
        if width < 3 or height < 2:
            return
        focused = self.focused_tile == tile
        _, background, _, border = self._style("workspace", "tile", "focused" if focused else "default", "text.primary", "surface.tile")
        if focused:
            border = "accent.primary"
        canvas.rect(x, y, width, height, border, background)
        title = {
            "metrics": "LIVE METRICS",
            "sensors": "SENSOR CATALOG",
            "history": "RECENT HISTORY",
            "alerts": "WATCHES & EVENTS",
        }.get(tile, tile.upper())
        title_fg, title_bg, title_bold, _ = self._style("workspace", "tile-title", "focused" if focused else "default", "accent.primary" if focused else "text.secondary", background)
        canvas.put(x + 2, y, f" {title} ", title_fg, title_bg, title_bold or True)
        if not compact and width >= 17:
            hide_x = x + width - 8
            max_x = x + width - 5
            canvas.put(max_x, y, "[□]", "text.secondary", background)
            canvas.put(hide_x, y, "[x]", "status.warning", background)
        self._register(x, y, width, height, "tile_focus", tile)
        if not compact and width >= 17:
            self._register(max_x, y, 3, 1, "tile_maximize", tile)
            self._register(hide_x, y, 3, 1, "tile_hide", tile)
        content_x, content_y = x + 2, y + 1
        content_width = max(0, width - 4)
        content_height = max(0, height - 2)
        if tile == "metrics":
            self._draw_metrics_tile(canvas, content_x, content_y, content_width, content_height, records)
        elif tile == "sensors":
            self._draw_sensor_table(canvas, content_x, content_y, content_width, content_height, records, mini=True)
        elif tile == "history":
            self._draw_history_graph(canvas, content_x, content_y, content_width, content_height, records, mini=True)
        elif tile == "alerts":
            self._draw_watch_list(canvas, content_x, content_y, content_width, content_height, records, mini=True)

    @staticmethod
    def _category(record: SensorRecord) -> str:
        text = f"{record.reading.sensor} {record.reading.label}".lower()
        if any(word in text for word in ("cpu", "processor", "core", "package")):
            return "CPU"
        if any(word in text for word in ("gpu", "graphics", "video card", "radeon", "geforce")):
            return "GPU"
        if any(word in text for word in ("memory", "ram", "dimm")):
            return "Memory"
        if any(word in text for word in ("drive", "disk", "ssd", "nvme", "storage")):
            return "Storage"
        if any(word in text for word in ("fan", "pump", "cooler")) or record.reading.kind == "fan":
            return "Cooling"
        if any(word in text for word in ("network", "ethernet", "wifi", "wi-fi")):
            return "Network"
        return "Other"

    def _ordered_records(self, records: dict[str, SensorRecord]) -> list[SensorRecord]:
        pinned = self.config.get("pinned", [])
        priority = {name: index for index, name in enumerate(("CPU", "GPU", "Memory", "Storage", "Cooling", "Network", "Other"))}
        rows = list(records.values())
        if self.filter_text:
            needle = self.filter_text.casefold()
            rows = [row for row in rows if needle in row.reading.sensor.casefold() or needle in row.reading.label.casefold() or needle in row.reading.unit.casefold()]
        return sorted(rows, key=lambda row: (0 if row.reading.key in pinned else 1, priority[self._category(row)], row.reading.sensor.casefold(), row.reading.label.casefold()))

    def _featured_records(self, records: dict[str, SensorRecord]) -> list[SensorRecord]:
        pinned = [records[key] for key in self.config.get("pinned", []) if key in records]
        if pinned:
            return pinned[:8]
        excluded = set(self.config.get("excluded_metrics", []))
        rows = [record for key, record in records.items() if key not in excluded]
        selected: list[SensorRecord] = []
        searches = [
            (lambda r: r.reading.kind == "usage" and self._category(r) == "CPU", ("total", "utility", "load")),
            (lambda r: r.reading.kind == "temperature" and self._category(r) == "CPU", ("package", "average", "cpu")),
            (lambda r: r.reading.kind == "usage" and self._category(r) == "GPU", ("core", "gpu", "load")),
            (lambda r: r.reading.kind == "temperature" and self._category(r) == "GPU", ("gpu", "hot spot", "temperature")),
        ]
        for predicate, hints in searches:
            candidates = [row for row in rows if predicate(row) and row not in selected]
            candidates.sort(key=lambda row: (min((0 if hint in row.reading.label.lower() else 1 for hint in hints), default=1), row.reading.label.lower()))
            if candidates:
                selected.append(candidates[0])
        for row in sorted(rows, key=lambda item: (item.reading.kind not in {"usage", "temperature"}, item.reading.sensor.casefold(), item.reading.label.casefold())):
            if row not in selected:
                selected.append(row)
            if len(selected) >= 4:
                break
        return selected[:4]

    def _format_value(self, value: float | None, unit: str = "") -> str:
        if value is None or not math.isfinite(value):
            return "—"
        if abs(value) >= 10_000 and unit not in {"RPM", "MHz", "kHz"}:
            body = f"{value / 1000:.1f}k"
        elif abs(value) >= 1000:
            body = f"{value:,.0f}"
        elif abs(value) >= 100:
            body = f"{value:.0f}"
        else:
            body = f"{value:.1f}"
        return f"{body} {unit}".rstrip()

    def _value_role(self, record: SensorRecord, now: float | None = None) -> str:
        if self.store.quality(record, now) != "good":
            return "status.warning"
        value = record.value
        if value is not None and record.reading.kind == "temperature" and value >= 85:
            return "status.error"
        if value is not None and record.reading.kind == "usage" and value >= 95:
            return "status.warning"
        return "accent.primary"

    def _draw_metrics_tile(self, canvas: Canvas, x: int, y: int, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        if width < 5 or height < 2:
            return
        featured = self._featured_records(records)
        if not featured:
            if records:
                canvas.put(x, y, "No live metrics selected", "text.secondary", "surface.tile", max_width=width)
                canvas.put(x, y + 1, "Double-click a sensor row to add one", "text.disabled", "surface.tile", max_width=width)
            else:
                canvas.put(x, y, "Waiting for HWiNFO readings…", "text.secondary", "surface.tile", max_width=width)
                canvas.put(x, y + 1, "Start Sensors and enable Shared Memory Support", "text.disabled", "surface.tile", max_width=width)
            return
        now = time.time()
        labels_in_bar = bool(self.config.get("metric_bar_labels", False))
        row_height = 1 if labels_in_bar else (2 if height >= 6 else 1)
        count = min(len(featured), max(1, height // row_height))
        for index, record in enumerate(featured[:count]):
            row_y = y + index * row_height
            value = self._format_value(record.value, record.reading.unit)
            role = self._value_role(record, now)
            self._register(x, row_y, width, row_height, "metric_select", record.reading.key)
            if labels_in_bar:
                bar_width = width
                fraction = max(0.0, min(1.0, (record.value or 0.0) / 100.0))
                completed = min(bar_width, round(fraction * bar_width))
                self._draw_metric_bar(canvas, x, row_y, bar_width, completed)
                value_cells = min(bar_width, sum(cell_width(char) for char in value))
                gap = 1 if value_cells < bar_width else 0
                label_cells = max(0, bar_width - value_cells - gap)
                if label_cells:
                    self._put_bar_text(canvas, x, row_y, record.reading.label, label_cells, x, bar_width, completed)
                value_x = x + max(0, bar_width - value_cells)
                self._put_bar_text(canvas, value_x, row_y, value, value_cells, x, bar_width, completed)
                continue
            pinned = "◆ " if record.reading.key in self.config.get("pinned", []) else ""
            name = f"{pinned}{record.reading.label}"
            value_width = min(width // 2, len(value))
            label_width = max(1, width - value_width - 2)
            canvas.put(x, row_y, name, "text.primary", "surface.tile", max_width=label_width)
            canvas.put(x + label_width + 1, row_y, value, role, "surface.tile", True, max_width=value_width)
            if row_height == 2:
                fraction = max(0.0, min(1.0, (record.value or 0.0) / 100.0))
                bar_width = max(1, width - 2)
                completed = min(bar_width, round(fraction * bar_width))
                self._draw_metric_bar(canvas, x, row_y + 1, bar_width, completed)

    def _lut_colour_at(self, position: int, width: int) -> str:
        if not self.theme.lut:
            return "accent.primary"
        index = round(max(0, position) * (len(self.theme.lut) - 1) / max(1, width - 1))
        return self.theme.lut[min(len(self.theme.lut) - 1, max(0, index))]

    def _draw_metric_bar(self, canvas: Canvas, x: int, y: int, width: int, completed: int) -> None:
        if width <= 0:
            return
        fill_char, rest_char = ("█", "░") if not self.ascii_only else ("#", ".")
        for offset in range(width):
            if offset < completed:
                canvas.put(x + offset, y, fill_char, self._lut_colour_at(offset, width), "surface.raised")
            else:
                canvas.put(x + offset, y, rest_char, "text.disabled", "surface.raised")

    def _put_bar_text(self, canvas: Canvas, x: int, y: int, text: str, max_width: int, bar_x: int, bar_width: int, completed: int) -> None:
        cursor = x
        end = x + max(0, max_width)
        rest_text = self._contrast_text_role("surface.raised")
        for char in text:
            if cursor >= end:
                break
            offset = cursor - bar_x
            on_fill = 0 <= offset < completed
            background = self._lut_colour_at(offset, bar_width) if on_fill else "surface.raised"
            foreground = self._contrast_text_role(background) if on_fill else rest_text
            next_cursor = canvas.put(cursor, y, char, foreground, background, True, max_width=end - cursor)
            if next_cursor > cursor:
                cursor = next_cursor
            elif cell_width(char) > 0:
                break

    def _contrast_text_role(self, background_role: str) -> str:
        def luminance(role: str) -> float:
            colour = self.theme.colour(role)
            channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        background = luminance(background_role)
        choices = ("text.primary", "text.inverse")
        return max(choices, key=lambda role: (max(luminance(role), background) + 0.05) / (min(luminance(role), background) + 0.05))

    def _draw_sensor_table(self, canvas: Canvas, x: int, y: int, width: int, height: int, records: dict[str, SensorRecord], mini: bool = False) -> None:
        if width < 8 or height < 1:
            return
        ordered = self._ordered_records(records)
        pinned = set(self.config.get("pinned", []))
        if mini:
            summary = f"{len(ordered)} readings · {len(pinned)} pinned"
            canvas.put(x, y, summary, "text.secondary", "surface.tile", max_width=width)
            start = y + 1
            count = max(0, height - 1)
        else:
            header = f"{len(ordered)} readings   {len(pinned)} pinned   Filter: {self.filter_text or '—'}   / to edit"
            canvas.put(x, y, header, "text.secondary", "surface.base", max_width=width)
            start = y + 2
            canvas.put(x, y + 1, "PIN  READING".ljust(max(12, int(width * .48))) + "SENSOR".ljust(max(8, int(width * .23))) + "VALUE / UNIT       STATE", "accent.secondary", "surface.base", True, max_width=width)
            count = max(0, height - 2)
        offset = self.tile_scroll["sensors"]
        available = ordered[offset : offset + count]
        for row_index, record in enumerate(available):
            row_y = start + row_index
            if row_y >= canvas.height - 2:
                break
            selected = record.reading.key == self.selected_sensor
            background = "state.selected" if selected else "surface.tile" if mini else "surface.base"
            role = self._value_role(record)
            marker = "◆" if record.reading.key in pinned else "·"
            if self.ascii_only:
                marker = "*" if record.reading.key in pinned else "."
            label_w = max(12, int(width * 0.43)) if width > 40 else max(6, int(width * 0.45))
            sensor_w = max(6, int(width * 0.22)) if width > 40 else max(3, int(width * 0.15))
            value_text = self._format_value(record.value, record.reading.unit)
            state = self.store.quality(record)
            prefix = f"{marker} "
            row_fg, row_bg, row_bold, _ = self._style("content", "sensor-row", "default", "text.primary", "surface.tile" if mini else "surface.base")
            if selected:
                row_fg, row_bg, selected_bold, _ = self._style("overlay", "selection", "selected", row_fg, "state.selected")
                row_bold = row_bold or selected_bold or True
            else:
                row_bg = background if mini else row_bg
            canvas.put(x, row_y, prefix, "accent.primary" if record.reading.key in pinned else "text.disabled", row_bg, selected)
            canvas.put(x + 2, row_y, record.reading.label, row_fg, row_bg, selected or row_bold, max_width=label_w - 2)
            if not mini and width > 48:
                sx = x + label_w
                canvas.put(sx, row_y, record.reading.sensor, "text.secondary", row_bg, max_width=sensor_w)
                vx = sx + sensor_w
            else:
                vx = x + label_w
            canvas.put(vx, row_y, value_text, role, row_bg, True, max_width=max(1, width - (vx - x) - (8 if not mini else 0)))
            if not mini and width > 48:
                canvas.put(x + width - 7, row_y, state.upper(), "status.success" if state == "good" else "status.warning", row_bg)
            self._register(x, row_y, width, 1, "sensor_select", record.reading.key)
        if not ordered:
            text = "No matching sensors" if self.filter_text else "Waiting for HWiNFO shared memory…"
            canvas.put(x, start, text, "text.disabled", "surface.tile" if mini else "surface.base", max_width=width)

    def _history_columns(self, samples: list[tuple[float, float]], width: int) -> list[tuple[int, float, float, float]]:
        """Show one sample per column, advancing right until the oldest falls off."""
        if width < 1 or not samples:
            return []
        visible = samples[-width:]
        return [(index, value, value, value) for index, (_, value) in enumerate(visible)]

    def _draw_sparkline(self, canvas: Canvas, x: int, y: int, width: int, samples: list[tuple[float, float]], height: int = 1, background: str = "surface.tile", style: str = "block", unit: str = "") -> None:
        if height > 1:
            self._draw_history_chart(canvas, x, y, width, height, samples, background, style, unit)
            return
        columns = self._history_columns(samples, width)
        if not columns:
            return
        low, high = self._history_axis_range(columns, unit)
        blocks = "._:-=+*#" if self.ascii_only else "▁▂▃▄▅▆▇█"
        for index, _, _, value in columns:
            fraction = 0.5 if high == low else (value - low) / (high - low)
            level = min(len(blocks) - 1, max(0, round(fraction * (len(blocks) - 1))))
            glyph = blocks[level] if style == "block" else ("|" if self.ascii_only else "│") if style == "stem" else ("*" if self.ascii_only else "●")
            canvas.put(x + index, y, glyph, self._history_colour(value, low, high), background)

    def _history_axis_range(self, columns: list[tuple[int, float, float, float]], unit: str) -> tuple[float, float]:
        low = min(column[1] for column in columns)
        high = max(column[2] for column in columns)
        if unit.strip().casefold() in {"%", "percent", "pct"} and low >= 0:
            return 0.0, high if high > 0 else 1.0
        return low, high

    def _history_colour(self, value: float, low: float, high: float) -> str:
        fraction = 0.5 if high == low else min(1.0, max(0.0, (value - low) / (high - low)))
        index = round(fraction * (len(self.theme.lut) - 1))
        return self.theme.lut[min(len(self.theme.lut) - 1, max(0, index))]

    def _history_stats_width(self, width: int) -> int:
        if width < 24:
            return 0
        max_width = max(7, min(20, width - 12))
        return min(max_width, max(16, width // 3))

    def _metric_history_stats_width(self, key: str, width: int) -> int:
        default = self._history_stats_width(width)
        if not default:
            return 0
        max_width = max(7, min(20, width - 12))
        configured = self.config.get("history_stats_widths", {})
        try:
            value = int(configured.get(key, default)) if isinstance(configured, dict) else default
        except (TypeError, ValueError, OverflowError):
            value = default
        return min(max_width, max(7, value))

    def _recent_column_widths(self, width: int, columns: int) -> list[int]:
        available = max(columns, width - 2 - (columns - 1))
        extra = max(0, available - columns * 24)
        configured = self.config.get("history_column_widths", [])
        if not isinstance(configured, list) or len(configured) != columns:
            weights = [1.0] * columns
        else:
            weights = []
            for value in configured:
                try:
                    weight = float(value)
                except (TypeError, ValueError, OverflowError):
                    weight = 0.0
                weights.append(weight if math.isfinite(weight) and weight > 0 else 0.0)
            if not any(weights):
                weights = [1.0] * columns
        total = sum(weights)
        widths = [24 + int(extra * weight / total) for weight in weights]
        widths[-1] += available - sum(widths)
        return widths

    def _set_recent_column_widths(self, widths: list[int]) -> None:
        self.config["history_column_widths"] = [float(max(0, width - 24)) for width in widths]

    def _rolling_average(self, samples: list[tuple[float, float]], seconds: float) -> float | None:
        if not samples:
            return None
        latest = samples[-1][0]
        cutoff = latest - seconds
        if samples[0][0] > cutoff:
            return None
        values = [value for timestamp, value in samples if timestamp >= cutoff]
        return statistics.fmean(values) if values else None

    def _history_stats_values(self, record: SensorRecord, samples: list[tuple[float, float]], chart_width: int) -> list[tuple[str, str]]:
        columns = self._history_columns(samples, max(1, chart_width))
        if not columns:
            return []
        _, axis_peak = self._history_axis_range(columns, record.reading.unit)
        recorded_max = record.reading.maximum
        if recorded_max is None or not math.isfinite(recorded_max):
            recorded_max = max(value for _, value in samples) if samples else axis_peak
        result = [
            ("Y peak", self._format_value(axis_peak, record.reading.unit)),
            ("Rec max", self._format_value(recorded_max, record.reading.unit)),
        ]
        if self.config.get("history_show_averages"):
            for label, seconds in (("Avg 1m", 60.0), ("Avg 5m", 300.0)):
                average = self._rolling_average(samples, seconds)
                result.append((label, self._format_value(average, record.reading.unit) if average is not None else "—"))
        return result

    def _draw_history_stats_panel(
        self,
        canvas: Canvas,
        x: int,
        y: int,
        width: int,
        height: int,
        record: SensorRecord,
        samples: list[tuple[float, float]],
        chart_width: int,
        header: str | None = None,
    ) -> None:
        if width < 1 or height < 1:
            return
        vertical = "|" if self.ascii_only else "│"
        for row in range(height):
            canvas.put(x, y + row, vertical, "border.normal", "surface.tile")
        row_y = y
        if header:
            canvas.put(x + 2, row_y, header, "accent.primary", "surface.tile", True, max_width=max(0, width - 3))
            row_y += 1
        available_rows = max(0, height - (row_y - y))
        values = self._history_stats_values(record, samples, chart_width)
        if available_rows == 1 and values:
            max_width = max(0, width - 3)
            unit = record.reading.unit.replace(" ", "")
            pairs = [("Y", values[0][1]), ("M", values[1][1])]
            compact = " ".join(f"{label}{value.replace(' ', '')}" for label, value in pairs)
            if len(compact) > max_width:
                compact_values = []
                for label, value in pairs:
                    number = value.replace(" ", "")
                    if unit and number.endswith(unit):
                        number = number[: -len(unit)]
                    if number.endswith(".0"):
                        number = number[:-2]
                    compact_values.append((label, number))
                compact = " ".join(f"{label}{number}" for label, number in compact_values)
            canvas.put(x + 2, row_y, compact, "text.secondary", "surface.tile", max_width=max_width)
            return
        tiny = width < 10
        compact = width < 22
        compact_labels = {"Y peak": "Y", "Rec max": "M" if width < 17 else "Max", "Avg 1m": "1m", "Avg 5m": "5m"}
        content_width = max(0, width - (1 if tiny else 3))
        text_x = x + (1 if tiny else 2)
        for index, (label, value) in enumerate(values[:available_rows]):
            role = "accent.primary" if label == "Y peak" else "text.secondary"
            shown_label = compact_labels.get(label, label) if compact else label
            shown_value = value.replace(" ", "") if compact else value
            separator = "" if tiny else " "
            value_width = max(0, content_width - len(shown_label) - len(separator))
            if compact and len(shown_value) > value_width:
                unit = record.reading.unit.replace(" ", "")
                if unit and shown_value.endswith(unit):
                    number_only = shown_value[: -len(unit)]
                    if len(number_only) <= value_width:
                        shown_value = number_only
                if shown_value.endswith(".0") and len(shown_value) > value_width:
                    shown_value = shown_value[:-2]
                if len(shown_value) > value_width:
                    shown_value = shown_value[:value_width]
            text = f"{shown_label}{separator}{shown_value}" if compact else f"{shown_label:<7} {shown_value}"
            canvas.put(
                text_x,
                row_y + index,
                text,
                role,
                "surface.tile",
                max_width=content_width,
            )

    def _draw_history_chart(self, canvas: Canvas, x: int, y: int, width: int, height: int, samples: list[tuple[float, float]], background: str = "surface.tile", style: str = "block", unit: str = "") -> None:
        columns = self._history_columns(samples, width)
        if height < 1 or not columns:
            return
        low, high = self._history_axis_range(columns, unit)
        stem = "|" if self.ascii_only else "│"
        point = "*" if self.ascii_only else "●"
        bottom = y + height - 1

        def row_for(value: float) -> int:
            fraction = 0.5 if high == low else min(1.0, max(0.0, (value - low) / (high - low)))
            return bottom - round(fraction * (height - 1))

        for index, _, _, value in columns:
            colour = self._history_colour(value, low, high)
            point_y = row_for(value)
            if style == "block":
                fraction = 0.5 if high == low else min(1.0, max(0.0, (value - low) / (high - low)))
                eighths = min(height * 8, max(1, round(fraction * height * 8)))
                full_cells, partial_eighths = divmod(eighths, 8)
                full_glyph = "#" if self.ascii_only else "█"
                partial_glyphs = "._:-=+*" if self.ascii_only else "▁▂▃▄▅▆▇"
                for offset in range(full_cells):
                    canvas.put(x + index, bottom - offset, full_glyph, colour, background)
                if partial_eighths and full_cells < height:
                    cap_y = bottom - full_cells
                    canvas.put(x + index, cap_y, partial_glyphs[partial_eighths - 1], colour, background)
            elif style == "stem":
                for row_y in range(point_y + 1, bottom + 1):
                    canvas.put(x + index, row_y, stem, colour, background)
                canvas.put(x + index, point_y, point, colour, background, True)
            else:
                canvas.put(x + index, point_y, point, colour, background, True)

    def _history_values(self, key: str) -> list[tuple[float, float]]:
        return self.store.history_for(key)

    def _initialize_recent_history(self, records: dict[str, SensorRecord]) -> None:
        keys = self.config.setdefault("history_keys", [])
        if self.config.get("history_initialized") or not records:
            return
        initial = records.get(self.selected_sensor) if self.selected_sensor else None
        if initial is None:
            featured = self._featured_records(records)
            initial = featured[0] if featured else next(iter(records.values()))
            self.selected_sensor = initial.reading.key
        if initial.reading.key not in keys:
            keys.insert(0, initial.reading.key)
        del keys[8:]
        self.config["history_initialized"] = True
        self._save()

    def _draw_history_graph(self, canvas: Canvas, x: int, y: int, width: int, height: int, records: dict[str, SensorRecord], mini: bool = False) -> None:
        if mini:
            self._initialize_recent_history(records)
            keys = self.config.setdefault("history_keys", [])
            history_records = [records[key] for key in keys if key in records]
            if not history_records:
                message = "Double-click a live metric to add it here" if records else "History appears after the first HWiNFO update."
                canvas.put(x, y, message, "text.secondary", "surface.tile", max_width=width)
                if height > 1 and records:
                    canvas.put(x, y + 1, "Double-right-click a row to remove it", "text.disabled", "surface.tile", max_width=width)
                return
            selected_record = next((record for record in history_records if record.reading.key == self.selected_sensor), history_records[0])
            panel_width = self._history_stats_width(width)
            chart_width = max(1, width - panel_width - (1 if panel_width else 0))
            offset = min(max(0, len(history_records) - 1), max(0, self.tile_scroll.get("history", 0)))
            row_y = y
            for record in history_records[offset:]:
                chart_rows = min(self._history_chart_rows(record.reading.key), max(0, height - 1))
                item_height = 1 + chart_rows
                remaining = y + height - row_y
                if remaining < 1:
                    break
                if item_height > remaining:
                    if row_y != y:
                        break
                    item_height = remaining
                    chart_rows = max(0, item_height - 1)
                current = self._format_value(record.value, record.reading.unit)
                current_width = min(chart_width, sum(cell_width(char) for char in current))
                label_width = max(0, chart_width - current_width - (1 if current_width < chart_width else 0))
                canvas.put(x, row_y, record.reading.label, "text.primary", "surface.tile", max_width=label_width)
                current_x = x + max(0, chart_width - current_width)
                canvas.put(current_x, row_y, current, self._value_role(record), "surface.tile", True, max_width=current_width)
                self._register(x, row_y, chart_width, item_height, "history_select", record.reading.key)
                if chart_rows > 0:
                    samples = self._history_values(record.reading.key)
                    self._draw_sparkline(
                        canvas,
                        x,
                        row_y + 1,
                        chart_width,
                        samples,
                        chart_rows,
                        style=self._history_chart_style(record.reading.key),
                        unit=record.reading.unit,
                    )
                row_y += item_height
            if panel_width:
                selected_samples = self._history_values(selected_record.reading.key)
                self._draw_history_stats_panel(
                    canvas,
                    x + chart_width,
                    y,
                    panel_width,
                    height,
                    selected_record,
                    selected_samples,
                    chart_width,
                    header=selected_record.reading.label,
                )
            return
        record = records.get(self.selected_sensor) if self.selected_sensor else None
        if record is None and records:
            featured = self._featured_records(records)
            record = featured[0] if featured else next(iter(records.values()))
            self.selected_sensor = record.reading.key
        if not record:
            canvas.put(x, y, "History appears after the first HWiNFO update.", "text.secondary", "surface.tile", max_width=width)
            return
        values = self._history_values(record.reading.key)
        nums = [value for _, value in values]
        canvas.put(x, y, f"{record.reading.sensor}  /  {record.reading.label}", "accent.primary", "surface.base", True, max_width=width)
        canvas.put(x, y + 1, f"Current  {self._format_value(record.value, record.reading.unit)}   ·   State  {self.store.quality(record).upper()}", self._value_role(record), "surface.base", True, max_width=width)
        if not nums:
            canvas.put(x, y + 3, "Waiting for samples…", "text.secondary", "surface.base", max_width=width)
            return
        low, high = min(nums), max(nums)
        mean = statistics.fmean(nums)
        summary = f"Min {self._format_value(low, record.reading.unit)}   Avg {self._format_value(mean, record.reading.unit)}   Max {self._format_value(high, record.reading.unit)}   Samples {len(nums)}"
        canvas.put(x, y + 2, summary, "text.secondary", "surface.base", max_width=width)
        graph_y = y + 4
        if height > 5:
            canvas.rect(x, graph_y, width, height - 5, "border.normal", "surface.tile")
            inner_width = max(1, width - 4)
            panel_width = self._history_stats_width(inner_width)
            graph_width = max(1, inner_width - panel_width - (1 if panel_width else 0))
            chart_columns = self._history_columns(values, graph_width)
            axis_low, axis_high = self._history_axis_range(chart_columns, record.reading.unit)
            graph_height = max(1, height - 7)
            self._draw_history_chart(
                canvas,
                x + 2,
                graph_y + 1,
                graph_width,
                graph_height,
                values,
                style=self._history_chart_style(record.reading.key),
                unit=record.reading.unit,
            )
            if panel_width:
                self._draw_history_stats_panel(
                    canvas,
                    x + 2 + graph_width,
                    graph_y + 1,
                    panel_width,
                    graph_height,
                    record,
                    values,
                    graph_width,
                )
            canvas.put(x + 2, graph_y + height - 6, f"{axis_low:.2f}", "text.secondary", "surface.tile")
            axis_high_label = f"{axis_high:.2f}"
            canvas.put(x + 2 + max(0, graph_width - len(axis_high_label)), graph_y + height - 6, axis_high_label, "text.secondary", "surface.tile")

    def _sparkline_for_height(self, values: list[float], width: int, height: int) -> list[str]:
        if not values or width < 1 or height < 1:
            return []
        low, high = min(values), max(values)
        if len(values) > width:
            values = [values[round(i * (len(values) - 1) / max(1, width - 1))] for i in range(width)]
        if high == low:
            return ["─" * len(values)] if not self.ascii_only else ["-" * len(values)]
        rows = [" " * len(values) for _ in range(height)]
        mutable = [list(row) for row in rows]
        for index, value in enumerate(values):
            level = min(height - 1, int((value - low) / (high - low) * (height - 1)))
            row = height - level - 1
            mutable[row][index] = "*" if self.ascii_only else "•"
            for below in range(row + 1, height):
                mutable[below][index] = "|" if self.ascii_only else "│"
        return ["".join(row) for row in mutable]

    def _draw_watch_list(self, canvas: Canvas, x: int, y: int, width: int, height: int, records: dict[str, SensorRecord], mini: bool = False) -> None:
        watches = self.config.get("watches", [])
        if not watches:
            canvas.put(x, y, "No threshold watches", "text.secondary", "surface.tile", max_width=width)
            if height > 1:
                canvas.put(x, y + 1, "Select a sensor and press W to add one", "text.disabled", "surface.tile", max_width=width)
        rows_for_watches = min(len(watches), max(0, height - (1 if mini else 3)))
        for index, watch in enumerate(watches[:rows_for_watches]):
            row_y = y + index
            if row_y >= canvas.height - 2:
                break
            record = records.get(watch.get("sensor"))
            active = bool(watch.get("active"))
            current = record.value if record else None
            state = "ACTIVE" if active else "ARMED"
            if not active and watch.get("since") is not None:
                state = "PENDING"
            text = f"{state:7} {watch.get('label', 'sensor')} ≥ {watch.get('above', '?')} / reset ≤ {watch.get('below', '?')}"
            if current is not None:
                text += f"  {self._format_value(current, record.reading.unit)}"
            role = "status.error" if active else "status.warning" if state == "PENDING" else "status.success"
            background = "state.selected" if index == self.watch_index and self.view == "alerts" else "surface.tile" if mini else "surface.base"
            canvas.put(x, row_y, text, role, background, active, max_width=width)
            self._register(x, row_y, width, 1, "watch_select", index)
        if not mini:
            events_y = y + max(3, rows_for_watches + 1)
            canvas.put(x, events_y, "RECENT EVENTS", "accent.secondary", "surface.base", True)
            for index, event in enumerate(self.config.get("events", [])[: max(0, height - (events_y - y) - 1)]):
                at = str(event.get("time", ""))
                if "T" in at:
                    at = at.split("T", 1)[1][:8]
                state = str(event.get("state", "")).upper()
                line = f"{at:8} {state:8} {event.get('label', '')}  {event.get('value', '')}"
                canvas.put(x, events_y + index + 1, line, "status.warning" if state == "TRIGGERED" else "text.secondary", "surface.base", state == "TRIGGERED", max_width=width)

    def _draw_sensors_page(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        canvas.put(1, 3, "ALL HWiNFO READINGS", "accent.primary", "surface.base", True)
        self._register(1, 3, width - 2, 1, "command", "sensor.filter")
        table_y = 4
        self._draw_sensor_table(canvas, 1, table_y, width - 2, height - table_y - 2, records, mini=False)
        ordered = self._ordered_records(records)
        if ordered and self.selected_sensor not in [record.reading.key for record in ordered]:
            self.selected_sensor = ordered[0].reading.key

    def _draw_history_page(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        if records and self.selected_sensor not in records:
            featured = self._featured_records(records)
            self.selected_sensor = featured[0].reading.key if featured else next(iter(records))
        average_state = "on" if self.config.get("history_show_averages") else "off"
        canvas.put(1, 3, f"LIVE HISTORY · SELECT A SENSOR WITH ↑ / ↓ · A averages {average_state}", "accent.primary", "surface.base", True)
        self._register(1, 3, width - 2, 1, "command", "sensor.filter")
        self._draw_history_graph(canvas, 2, 5, width - 4, height - 8, records, mini=False)

    def _draw_recent_page(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        self._initialize_recent_history(records)
        canvas.put(1, 3, "RECENT HISTORY", "accent.primary", "surface.base", True)
        configured_columns = min(6, max(1, int(self.config.get("history_columns", 3))))
        columns = min(configured_columns, max(1, min(6, (width - 3) // 24)))
        minus, plus = "[-]", "[+]"
        column_label = f"{columns} col"
        plus_x = width - len(plus) - 2
        label_x = plus_x - len(column_label) - 1
        minus_x = label_x - len(minus) - 1
        canvas.put(minus_x, 3, minus, "accent.secondary", "surface.base", True)
        self._register(minus_x, 3, len(minus), 1, "command", "history.columns_less")
        canvas.put(label_x, 3, column_label, "text.secondary", "surface.base")
        canvas.put(plus_x, 3, plus, "accent.secondary", "surface.base", True)
        self._register(plus_x, 3, len(plus), 1, "command", "history.columns_more")

        group_name = self._active_history_group()
        group_keys = self._active_history_keys()
        selected_key = self.selected_sensor if self.selected_sensor in group_keys else (group_keys[0] if group_keys else None)
        if selected_key:
            self.selected_sensor = selected_key
        selected_record = records.get(selected_key) if selected_key else None
        selected_label = selected_record.reading.label if selected_record else selected_key or "none"
        selected_rows = self._history_chart_rows(selected_key) if selected_key else 1
        selected_style = self._history_chart_style(selected_key) if selected_key else "block"
        chart_controls = f"{selected_label} · {selected_style.title()} {selected_rows}L · ,/. height ; style"
        split_hint = "| divider" if self.ascii_only else "↔ divider"
        averages_status = f"Avg {'on' if self.config.get('history_show_averages') else 'off'} [A] · drag {split_hint}"
        averages_x = max(2, width - len(averages_status) - 2)
        canvas.put(2, 5, chart_controls, "text.secondary", "surface.base", max_width=max(1, averages_x - 3))
        canvas.put(averages_x, 5, averages_status, "accent.secondary", "surface.base", max_width=width - averages_x - 2)
        previous_x = 2
        previous, following = "[<]", "[>]"
        canvas.put(previous_x, 4, previous, "accent.secondary", "surface.raised", True)
        self._register(previous_x, 4, len(previous), 1, "history_group_cycle", -1)
        name_x = previous_x + len(previous) + 1
        manage = "[ Manage groups · G ]"
        manage_x = max(name_x + 12, width - len(manage) - 2)
        name_text = f"Group: {group_name}  ·  {len(group_keys)} metric(s)"
        canvas.put(name_x, 4, name_text, "text.primary", "surface.raised", True, max_width=max(1, manage_x - name_x - len(following) - 2))
        next_x = manage_x - len(following) - 1
        canvas.put(next_x, 4, following, "accent.secondary", "surface.raised", True)
        self._register(next_x, 4, len(following), 1, "history_group_cycle", 1)
        canvas.put(manage_x, 4, manage, "accent.secondary", "surface.raised", True)
        self._register(manage_x, 4, len(manage), 1, "command", "history.group_manage")

        item_records = [records[key] for key in group_keys if key in records]
        if not item_records:
            if not self.config.get("history_keys"):
                message = "Double-click a Live Metrics row to add it to Recent History."
            elif group_name != "All":
                message = f"No metrics in {group_name}. Press G to manage group membership."
            else:
                message = "Waiting for the selected HWiNFO readings to return."
            canvas.put(2, 7, message, "text.secondary", "surface.base", max_width=width - 4)
            return

        grid_top = 6
        grid_bottom = height - 2
        grid_height = max(1, grid_bottom - grid_top)
        if grid_height < 6:
            canvas.put(2, grid_top, "Resize the terminal to show Recent History cards.", "status.warning", "surface.base", max_width=width - 4)
            return
        total_rows = math.ceil(len(item_records) / columns)
        scroll_row = min(max(0, total_rows - 1), max(0, self.tile_scroll.get("recent", 0)))
        column_widths = self._recent_column_widths(width, columns)
        column_xs: list[int] = []
        column_x = 1
        for column_width in column_widths:
            column_xs.append(column_x)
            column_x += column_width + 1

        average_rows = 4 if self.config.get("history_show_averages") else 2
        visible_card_count = 0
        for column, column_width in enumerate(column_widths):
            x = column_xs[column]
            y = grid_top
            column_records = item_records[column + scroll_row * columns :: columns]
            for record in column_records:
                chart_rows = self._history_chart_rows(record.reading.key)
                metric_stats_rows = average_rows if self._metric_history_stats_width(record.reading.key, column_width) else 0
                card_height = max(6, max(chart_rows, metric_stats_rows) + 4)
                remaining = grid_bottom - y
                if remaining < 6:
                    break
                rendered_height = min(card_height, remaining)
                self._draw_recent_metric_card(
                    canvas,
                    x,
                    y,
                    column_width,
                    rendered_height,
                    record,
                    record.reading.key == self.selected_sensor,
                    chart_rows,
                )
                visible_card_count += 1
                y += card_height + 1

        for column in range(columns - 1):
            divider_x = column_xs[column] + column_widths[column]
            grip = "↔" if not self.ascii_only else "|"
            canvas.put(divider_x, grid_top, grip, "accent.secondary", "surface.base", True)
            self._register(
                divider_x,
                grid_top,
                1,
                grid_height,
                "recent_column_resize",
                {"column": column, "widths": column_widths.copy()},
            )
        if visible_card_count < len(item_records) - scroll_row * columns:
            canvas.put(max(2, width - 30), height - 3, f"Card set {scroll_row + 1} of {total_rows} · wheel scrolls", "text.disabled", "surface.base", max_width=28)

    def _draw_recent_metric_card(
        self,
        canvas: Canvas,
        x: int,
        y: int,
        width: int,
        height: int,
        record: SensorRecord,
        selected: bool,
        chart_rows: int | None = None,
    ) -> None:
        border = "accent.primary" if selected else "border.normal"
        canvas.rect(x, y, width, height, border, "surface.tile")
        value = self._format_value(record.value, record.reading.unit)
        value_width = min(max(0, width - 4), sum(cell_width(char) for char in value))
        label_width = max(0, width - value_width - 5)
        canvas.put(x + 2, y + 1, record.reading.label, "text.primary", "surface.tile", selected, max_width=label_width)
        value_x = x + max(2, width - value_width - 2)
        canvas.put(value_x, y + 1, value, self._value_role(record), "surface.tile", True, max_width=value_width)
        samples = self._history_values(record.reading.key)
        values = [sample for _, sample in samples]
        plot_x, plot_y = x + 2, y + 2
        inner_width = max(1, width - 4)
        stats_width = self._metric_history_stats_width(record.reading.key, width)
        max_stats_width = max(7, min(20, width - 12)) if stats_width else 0
        plot_width = max(1, inner_width - stats_width)
        available_plot_height = max(1, height - 4)
        plot_height = min(available_plot_height, max(1, chart_rows)) if chart_rows is not None else available_plot_height
        if values:
            chart_columns = self._history_columns(samples, plot_width)
            axis_low, axis_high = self._history_axis_range(chart_columns, record.reading.unit)
            self._draw_history_chart(
                canvas,
                plot_x,
                plot_y,
                plot_width,
                plot_height,
                samples,
                style=self._history_chart_style(record.reading.key),
                unit=record.reading.unit,
            )
            footer = f"{len(values)} samples · Y {self._format_value(axis_low, record.reading.unit)}–{self._format_value(axis_high, record.reading.unit)}"
        else:
            canvas.put(plot_x, plot_y, "Collecting samples…", "text.disabled", "surface.tile", max_width=plot_width)
            footer = "No samples yet"
        if stats_width:
            self._draw_history_stats_panel(
                canvas,
                plot_x + plot_width,
                plot_y,
                stats_width,
                available_plot_height,
                record,
                samples,
                plot_width,
            )
        canvas.put(x + 2, y + height - 2, footer, "text.disabled", "surface.tile", max_width=max(0, width - 4))
        self._register(x, y, width, height, "recent_metric_select", record.reading.key)
        if stats_width:
            splitter_x = plot_x + plot_width
            splitter_y = plot_y + max(0, available_plot_height // 2)
            splitter = "↔" if not self.ascii_only else "|"
            canvas.put(splitter_x, splitter_y, splitter, "accent.secondary", "surface.tile", True)
            self._register(
                splitter_x,
                plot_y,
                1,
                available_plot_height,
                "recent_chart_split",
                {
                    "key": record.reading.key,
                    "stats_width": stats_width,
                    "max_stats_width": max_stats_width,
                },
            )
        resize_grip = "◢" if not self.ascii_only else "+"
        canvas.put(x + width - 1, y + height - 1, resize_grip, "accent.secondary", "surface.tile", True)
        self._register(x + width - 1, y + height - 1, 1, 1, "recent_card_resize", record.reading.key)

    def _draw_alerts_page(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        canvas.put(1, 3, f"THRESHOLD WATCHES · {len(self.config.get('watches', []))} configured", "accent.primary", "surface.base", True)
        add_label = "[ + add watch ]"
        canvas.put(max(1, width - len(add_label) - 2), 3, add_label, "accent.secondary", "surface.base", True)
        self._register(max(1, width - len(add_label) - 2), 3, len(add_label), 1, "command", "watch.add")
        self._draw_watch_list(canvas, 2, 5, width - 4, height - 8, records, mini=False)

    def _draw_palette_bar(self, canvas: Canvas, x: int, y: int, width: int) -> None:
        if width <= 0:
            return
        lut = self.theme.lut
        anchors = (self.theme_working or {}).get("palette", self.theme.palette).get("anchors", [])
        for column in range(width):
            idx = round(column * (len(lut) - 1) / max(1, width - 1))
            colour = lut[idx]
            canvas.put(x + column, y, " ", "text.primary", colour)
        if self.theme_editing and anchors:
            for index, anchor in enumerate(anchors):
                try:
                    position = float(anchor["position"])
                except (KeyError, TypeError, ValueError):
                    continue
                column = x + round(position * max(0, width - 1))
                mark = "▲" if not self.ascii_only else "^"
                canvas.put(column, y - 1, mark, "state.focus" if index == self.theme_anchor_index else "text.primary", "surface.base", index == self.theme_anchor_index)
                self._register(max(x, column - 1), y - 1, 3, 2, "theme_anchor", index)

    def _draw_theme_page(self, canvas: Canvas, width: int, height: int) -> None:
        canvas.put(1, 3, f"LUTEXskin-inspired themes · {self.theme_name} · {self.colour_mode}", "accent.primary", "surface.base", True)
        canvas.put(1, 4, "Metric bars use the full LUT; each history chart maps its visible value range across the full LUT.", "text.secondary", "surface.base", max_width=width - 2)
        self._draw_palette_bar(canvas, 2, 7, width - 4)
        if self.theme_editing and self.theme_working:
            palette = self.theme_working.get("palette", {})
            anchors = palette.get("anchors", [])
            canvas.put(1, 9, "PALETTE COMPOSER · anchor stops are the editable source; the LUT is sampled output", "accent.secondary", "surface.base", True, max_width=width - 2)
            for index, anchor in enumerate(anchors[:8]):
                row_y = 11 + index
                selected = index == self.theme_anchor_index
                colour = anchor.get("colour", "#ffffff")
                canvas.put(2, row_y, "●" if not self.ascii_only else "*", colour, "state.selected" if selected else "surface.base", selected)
                canvas.put(5, row_y, f"{index + 1}. {anchor.get('label') or 'anchor'}  {float(anchor.get('position', 0)):.3f}  {colour}  {'locked' if anchor.get('locked') else 'editable'}", "text.primary" if selected else "text.secondary", "state.selected" if selected else "surface.base", selected, max_width=width - 7)
                self._register(1, row_y, width - 2, 1, "theme_anchor", index)
            controls_y = min(height - 4, 11 + min(8, len(anchors)) + 1)
            cyclic = "on" if palette.get("cyclic") else "off"
            canvas.put(2, controls_y, f"Interpolation: {palette.get('interpolation', 'srgb')}   Cyclic: {cyclic}   Seed: {palette.get('seed', 0)}   Variation: {palette.get('variation', 0)}%", "text.primary", "surface.raised", max_width=width - 4)
            canvas.put(2, controls_y + 1, "← → select   ↑ ↓ hue   A add   X remove   L lock   H exact hex", "text.secondary", "surface.base", max_width=width - 4)
            canvas.put(2, controls_y + 2, ", . position   C cyclic   I interpolation   +/- variation   S seed", "text.secondary", "surface.base", max_width=width - 4)
            save_label, cancel_label = "[ commit · Enter ]", "[ cancel · Esc ]"
            canvas.put(2, controls_y + 4, save_label, "status.success", "surface.base", True)
            canvas.put(24, controls_y + 4, cancel_label, "status.warning", "surface.base", True)
            self._register(2, controls_y + 4, len(save_label), 1, "theme_commit")
            self._register(24, controls_y + 4, len(cancel_label), 1, "theme_cancel")
        else:
            canvas.put(2, 9, "THEMES", "accent.secondary", "surface.base", True)
            for index, name in enumerate(theme_names()):
                row_y = 11 + index
                selected = name == self.theme_name
                canvas.put(2, row_y, "●" if selected else "○", "accent.primary" if selected else "text.secondary", "state.selected" if selected else "surface.base", selected)
                canvas.put(5, row_y, name, "text.primary" if selected else "text.secondary", "state.selected" if selected else "surface.base", selected)
                self._register(1, row_y, width - 2, 1, "theme_select", name)
            canvas.put(2, 18, "E palette composer · T next theme", "text.secondary", "surface.base")
            canvas.put(2, 20, "Colour display degrades deterministically through truecolour, 256, 16, and monochrome.", "text.secondary", "surface.base", max_width=width - 4)

    def _draw_source_page(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord], status: SourceStatus) -> None:
        canvas.put(1, 3, "HWiNFO SHARED MEMORY SOURCE", "accent.primary", "surface.base", True)
        details = [
            ("Provider", "HWiNFO Sensors · Shared Memory Support"),
            ("Mapping", "Global\\HWiNFO_SENS_SM2 (read-only)"),
            ("Mutex", "Global\\HWiNFO_SM2_MUTEX"),
            ("State", f"{status.state.upper()} · {status.message}"),
            ("Readings", str(len(records))),
            ("Provider poll field", str(status.poll_time) if status.poll_time is not None else "not seen"),
            ("Samples", str(status.sample_count)),
            ("Poll interval", f"{self.config.get('poll_interval', 0.75):g} s"),
            ("Stale after", f"{self.config.get('stale_after', 5):g} s without a provider update"),
        ]
        for index, (label, value) in enumerate(details):
            y = 5 + index * 2
            canvas.put(2, y, label, "text.secondary", "surface.base", True, max_width=20)
            role = "status.success" if label == "State" and status.state == "connected" else "status.warning" if label == "State" and status.state == "stale" else "text.primary"
            canvas.put(23, y, value, role, "surface.base", max_width=width - 25)
        canvas.put(2, 5 + len(details) * 2 + 1, "HWiNFO remains the sensor provider. HWiNFOterm only opens the published mapping and mutex; it never changes HWiNFO settings.", "text.secondary", "surface.base", max_width=width - 4)
        canvas.put(2, 5 + len(details) * 2 + 3, "If the state is unavailable: open the Sensors window, check Shared Memory Support, then wait for the next poll.", "status.warning", "surface.base", max_width=width - 4)

    def _draw_help_page(self, canvas: Canvas, width: int, height: int) -> None:
        canvas.put(1, 3, "HWiNFOterm · KEYBOARD AND POINTER GUIDE", "accent.primary", "surface.base", True)
        lines = [
            "This is a native terminal dashboard. HWiNFOterm reads HWiNFO SM2 and does not install a background service.",
            "The Dashboard is a persistent tile workspace: focus, hide, restore, maximize, reorder, and resize its splits.",
            "All application commands appear in the mouse-operable command palette (Ctrl+P or the top-right Commands button).",
            "",
            "1 Dashboard   2 Sensors   3 History   7 Recent   4 Alerts   5 Theme   6 Source   ? Help",
            "Tab focus   M maximize   H hide   R restore   V move tile   0 reset layout   [ ] / - = adjust splits",
            "/ filter   P pin/unpin   W watch   B in-bar labels   G history groups   T theme   E palette   Q quit",
            "Recent: 7 tab   ←→ group   [ ] columns   G manage metric groups   ↑↓ select metric.",
            "Recent chart: ,/. set 1–8 rows; ; cycles Block/Stem/Point; A toggles 1m/5m averages.",
            "Recent cards: drag the chart/readout divider to resize plot width; the bottom-right corner changes chart height. Column gutters resize all cards in that column.",
            "Dashboard history tile: click a metric row to select it, then use the same chart controls.",
            "Mouse: double-click a sensor to add it to Live Metrics; double-click a metric to track its history.",
            "Double-right-click a Live Metrics or Recent History row to remove it; wheel scrolls the tile under the pointer.",
            "Drag split dividers or Recent card edges to resize. Escape cancels an active drag or closes a dialog.",
            "",
            "Sensor labels are treated as plain text. ANSI escapes and terminal control characters from readings are stripped.",
            "History is bounded by the configured window and stays in memory for this session. Threshold watches/events persist locally.",
            "The LUTEXskin-inspired theme editor previews anchor edits; Enter commits, Escape restores the last committed palette.",
            "Demo mode is synthetic and explicitly marked; it is never used as a fallback for missing HWiNFO data.",
        ]
        for index, line in enumerate(lines):
            if 5 + index >= height - 2:
                break
            role = "accent.secondary" if line.startswith(("1 Dashboard", "Tab focus", "Mouse:")) else "text.secondary"
            canvas.put(2, 5 + index, line, role, "surface.base", max_width=width - 4)

    def _filtered_commands(self) -> list[CommandSpec]:
        query = self.overlay_query.casefold().strip()
        rows = COMMANDS
        if query:
            rows = [
                command for command in COMMANDS
                if query in command.label.casefold()
                or query in command.category.casefold()
                or query in command.description.casefold()
                or any(query in key.casefold() for key in command.keys)
            ]
        return rows

    def _overlay_box(self, canvas: Canvas, width: int, height: int, box_width: int, box_height: int, title: str) -> tuple[int, int, int, int]:
        box_width = min(max(24, box_width), width - 2)
        box_height = min(max(7, box_height), height - 2)
        x = max(1, (width - box_width) // 2)
        y = max(1, (height - box_height) // 2)
        canvas.rect(x, y, box_width, box_height, "accent.primary", "surface.raised")
        canvas.put(x + 2, y, f" {title} ", "accent.primary", "surface.raised", True)
        self._register(x, y, box_width, box_height, "overlay_surface")
        close = "[×]"
        canvas.put(x + box_width - 6, y, close, "status.warning", "surface.raised")
        self._register(x + box_width - 6, y, 4, 1, "palette_close")
        return x, y, box_width, box_height

    def _draw_overlay(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        if self.overlay == "palette":
            self._draw_command_palette(canvas, width, height)
        elif self.overlay == "filter":
            self._draw_filter_overlay(canvas, width, height)
        elif self.overlay == "watch_form":
            self._draw_watch_form(canvas, width, height)
        elif self.overlay == "theme_hex":
            self._draw_theme_hex(canvas, width, height)
        elif self.overlay == "watch_delete":
            self._draw_watch_delete(canvas, width, height)
        elif self.overlay == "history_groups":
            self._draw_history_groups(canvas, width, height, records)
        elif self.overlay == "history_group_name":
            self._draw_history_group_name(canvas, width, height)
        elif self.overlay == "history_group_delete":
            self._draw_history_group_delete(canvas, width, height)

    def _draw_command_palette(self, canvas: Canvas, width: int, height: int) -> None:
        commands = self._filtered_commands()
        box_height = min(height - 2, max(11, min(19, len(commands) + 5)))
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(76, width - 4), box_height, "COMMAND PALETTE")
        prompt = "> " + self.overlay_query
        canvas.fill(x + 2, y + 2, box_width - 4, " ", "text.primary", "surface.tile")
        canvas.put(x + 3, y + 2, prompt, "text.primary", "surface.tile", max_width=box_width - 6)
        visible_rows = max(1, box_height - 5)
        first = max(0, min(max(0, len(commands) - visible_rows), self.overlay_index - visible_rows + 1))
        for offset, command in enumerate(commands[first : first + visible_rows]):
            row_y = y + 3 + offset
            selected = first + offset == self.overlay_index
            background = "state.selected" if selected else "surface.raised"
            key = ", ".join(command.keys)
            canvas.put(x + 2, row_y, f"{command.category:10} {command.label}", "text.primary" if selected else "text.secondary", background, selected, max_width=box_width - 18)
            canvas.put(x + box_width - 15, row_y, key, "accent.secondary", background, selected, max_width=12)
            self._register(x + 1, row_y, box_width - 2, 1, "palette_command", command.id)
        footer_y = y + box_height - 2
        canvas.put(x + 2, footer_y, f"{len(commands)} command(s) · Enter run · Esc close · pointer selects", "text.disabled", "surface.raised", max_width=box_width - 4)

    def _draw_filter_overlay(self, canvas: Canvas, width: int, height: int) -> None:
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(64, width - 4), 9, "FILTER SENSORS")
        canvas.put(x + 2, y + 2, "Reading, sensor, or unit contains:", "text.secondary", "surface.raised")
        canvas.fill(x + 2, y + 3, box_width - 4, " ", "text.primary", "surface.tile")
        canvas.put(x + 3, y + 3, self.overlay_query or "type to search…", "text.primary" if self.overlay_query else "text.disabled", "surface.tile", max_width=box_width - 6)
        label = "[ Apply · Enter ]"
        canvas.put(x + 2, y + box_height - 2, label, "status.success", "surface.raised", True)
        self._register(x + 2, y + box_height - 2, len(label), 1, "filter_apply")

    def _draw_watch_form(self, canvas: Canvas, width: int, height: int) -> None:
        form = self.watch_form
        if not form:
            return
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(72, width - 4), 14, "ADD THRESHOLD WATCH")
        canvas.put(x + 2, y + 2, f"Sensor: {form['label']}  ·  {form.get('unit', '')}", "accent.secondary", "surface.raised", True, max_width=box_width - 4)
        labels = ["Trigger when value is at/above", "Rearm when value is at/below", "Continuous duration (seconds)"]
        for index, label in enumerate(labels):
            compact = box_height < 13
            row_y = y + (3 + index if compact else 4 + index * 2)
            active = index == self.watch_form_field
            background = "state.selected" if active else "surface.raised"
            canvas.put(x + 2, row_y, label, "text.primary" if active else "text.secondary", background, active, max_width=box_width - 20)
            canvas.fill(x + box_width - 15, row_y, 12, " ", "text.primary", "surface.tile")
            canvas.put(x + box_width - 14, row_y, form["fields"][index], "text.primary", "surface.tile", True, max_width=10)
            self._register(x + 2, row_y, box_width - 4, 1, "watch_field", index)
        save = "[ Save · Enter ]"
        cancel = "[ Cancel · Esc ]"
        canvas.put(x + 2, y + box_height - 2, save, "status.success", "surface.raised", True)
        canvas.put(x + 22, y + box_height - 2, cancel, "status.warning", "surface.raised", True)
        self._register(x + 2, y + box_height - 2, len(save), 1, "watch_save")
        self._register(x + 22, y + box_height - 2, len(cancel), 1, "watch_cancel")

    def _draw_watch_delete(self, canvas: Canvas, width: int, height: int) -> None:
        watches = self.config.get("watches", [])
        if not watches:
            self.overlay = None
            return
        index = min(self.watch_delete_index, len(watches) - 1)
        watch = watches[index]
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(60, width - 4), 9, "REMOVE WATCH?")
        canvas.put(x + 2, y + 2, f"Remove {watch.get('label', 'this sensor')} watch?", "text.primary", "surface.raised", True, max_width=box_width - 4)
        yes, no = "[ Remove · Enter ]", "[ Keep · Esc ]"
        canvas.put(x + 2, y + 5, yes, "status.error", "surface.raised", True)
        canvas.put(x + 24, y + 5, no, "text.secondary", "surface.raised")
        self._register(x + 2, y + 5, len(yes), 1, "watch_delete_confirm")
        self._register(x + 24, y + 5, len(no), 1, "watch_delete_cancel")

    def _draw_history_groups(self, canvas: Canvas, width: int, height: int, records: dict[str, SensorRecord]) -> None:
        keys = self.config.get("history_keys", [])
        if keys:
            self.group_metric_index = min(max(0, self.group_metric_index), len(keys) - 1)
        else:
            self.group_metric_index = 0
        box_height = min(height - 2, max(12, min(21, len(keys) + 10)))
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(88, width - 4), box_height, "MANAGE RECENT HISTORY GROUPS")
        active = self._active_history_group()
        canvas.put(x + 2, y + 2, "Active group", "text.secondary", "surface.raised")
        previous, following = "[<]", "[>]"
        canvas.put(x + 2, y + 3, previous, "accent.secondary", "surface.raised", True)
        self._register(x + 2, y + 3, len(previous), 1, "history_group_cycle", -1)
        group_label = f"{active}  ·  {len(self._history_groups()) + 1} groups"
        canvas.put(x + 7, y + 3, group_label, "text.primary", "surface.raised", True, max_width=max(1, box_width - 20))
        self._register(x + 7, y + 3, max(1, box_width - 20), 1, "history_group_cycle", 1)
        following_x = x + box_width - 7
        canvas.put(following_x, y + 3, following, "accent.secondary", "surface.raised", True)
        self._register(following_x, y + 3, len(following), 1, "history_group_cycle", 1)

        actions = (
            [("[ New · N ]", "new"), ("[ Rename · R ]", "rename"), ("[ Delete · D ]", "delete"), ("[ Done · Esc ]", "done")]
            if box_width >= 64
            else [("[N]", "new"), ("[R]", "rename"), ("[D]", "delete"), ("[Done]", "done")]
        )
        action_x = x + 2
        for label, action in actions:
            if action_x + len(label) >= x + box_width - 1:
                break
            role = "status.error" if action == "delete" else "status.success" if action == "new" else "accent.secondary"
            canvas.put(action_x, y + 5, label, role, "surface.raised", True)
            self._register(action_x, y + 5, len(label), 1, "history_group_action", action)
            action_x += len(label) + 2

        list_y = y + 7
        list_height = max(0, box_height - 10)
        canvas.put(x + 2, y + 6, "Recent metrics · click a row or press Enter to toggle membership", "text.secondary", "surface.raised", max_width=box_width - 4)
        list_start = min(max(0, len(keys) - list_height), max(0, self.group_metric_index - list_height + 1))
        visible_keys = keys[list_start : list_start + list_height]
        group = next((item for item in self._history_groups() if item.get("name") == active), None)
        members = set(group.get("metrics", [])) if group else set()
        for index, key in enumerate(visible_keys):
            record = records.get(key)
            label = record.reading.label if record else key
            value = f"  {self._format_value(record.value, record.reading.unit)}" if record else "  (not connected)"
            checked = key in members
            marker = "✓" if checked else "□"
            if self.ascii_only:
                marker = "*" if checked else "[ ]"
            text = f"{marker}  {label}{value}"
            selected = list_start + index == self.group_metric_index
            background = "state.selected" if selected else "surface.raised"
            canvas.put(x + 2, list_y + index, text, "text.primary" if selected else "text.secondary", background, selected, max_width=box_width - 4)
            self._register(x + 2, list_y + index, box_width - 4, 1, "history_group_metric", key)
        if not keys:
            canvas.put(x + 3, list_y, "Add metrics from Live Metrics before making groups.", "text.disabled", "surface.raised", max_width=box_width - 6)
        canvas.put(x + 2, y + box_height - 2, "↑↓ select metric   ←→ switch group   Enter/Space toggle   G close", "text.disabled", "surface.raised", max_width=box_width - 4)

    def _draw_history_group_name(self, canvas: Canvas, width: int, height: int) -> None:
        title = "NEW HISTORY GROUP" if self.group_name_mode == "new" else "RENAME HISTORY GROUP"
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(66, width - 4), 10, title)
        canvas.put(x + 2, y + 2, "Group name (up to 32 characters):", "text.secondary", "surface.raised", max_width=box_width - 4)
        canvas.fill(x + 2, y + 4, box_width - 4, " ", "text.primary", "surface.tile")
        canvas.put(x + 3, y + 4, self.overlay_query or "type a name…", "text.primary" if self.overlay_query else "text.disabled", "surface.tile", max_width=box_width - 6)
        save, cancel = "[ Save · Enter ]", "[ Cancel · Esc ]"
        canvas.put(x + 2, y + box_height - 2, save, "status.success", "surface.raised", True)
        canvas.put(x + 23, y + box_height - 2, cancel, "status.warning", "surface.raised")
        self._register(x + 2, y + box_height - 2, len(save), 1, "history_group_name_save")
        self._register(x + 23, y + box_height - 2, len(cancel), 1, "history_group_name_cancel")

    def _draw_history_group_delete(self, canvas: Canvas, width: int, height: int) -> None:
        name = self.group_delete_name or "this group"
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(62, width - 4), 9, "DELETE HISTORY GROUP?")
        canvas.put(x + 2, y + 2, f"Delete {name}? Its metrics remain in Recent History.", "text.primary", "surface.raised", True, max_width=box_width - 4)
        yes, no = "[ Delete · Enter ]", "[ Keep · Esc ]"
        canvas.put(x + 2, y + 5, yes, "status.error", "surface.raised", True)
        canvas.put(x + 24, y + 5, no, "text.secondary", "surface.raised")
        self._register(x + 2, y + 5, len(yes), 1, "history_group_delete_confirm")
        self._register(x + 24, y + 5, len(no), 1, "history_group_delete_cancel")

    def _draw_theme_hex(self, canvas: Canvas, width: int, height: int) -> None:
        x, y, box_width, box_height = self._overlay_box(canvas, width, height, min(58, width - 4), 9, "EXACT ANCHOR COLOUR")
        canvas.put(x + 2, y + 2, "Enter a six-digit RGB colour in #RRGGBB form.", "text.secondary", "surface.raised", max_width=box_width - 4)
        canvas.fill(x + 2, y + 3, 18, " ", "text.primary", "surface.tile")
        canvas.put(x + 3, y + 3, self.theme_colour_draft, "text.primary", "surface.tile", True, max_width=16)
        preview = self.theme_colour_draft if len(self.theme_colour_draft) == 7 else "surface.raised"
        canvas.fill(x + 23, y + 3, 8, " ", "text.primary", preview)
        save, cancel = "[ Apply · Enter ]", "[ Cancel · Esc ]"
        canvas.put(x + 2, y + box_height - 2, save, "status.success", "surface.raised", True)
        canvas.put(x + 22, y + box_height - 2, cancel, "status.warning", "surface.raised")
        self._register(x + 2, y + box_height - 2, len(save), 1, "theme_hex_save")
        self._register(x + 22, y + box_height - 2, len(cancel), 1, "theme_hex_cancel")
