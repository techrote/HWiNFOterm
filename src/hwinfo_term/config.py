"""Small, defensive user configuration for the terminal workspace."""

from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


TILES = ["metrics", "sensors", "history", "alerts"]
MAX_HISTORY_CHART_ROWS = 8
HISTORY_CHART_STYLES = ("block", "stem", "point")
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "poll_interval": 0.75,
    "stale_after": 5.0,
    "history_seconds": 900,
    "pinned": [],
    "excluded_metrics": [],
    "history_keys": [],
    "cards_keys": [],
    "cards_initialized": False,
    "cards_columns": 3,
    "cards_column_widths": [],
    "chrome_hidden": False,
    "history_chart_rows": {},
    "history_chart_styles": {},
    "history_show_averages": False,
    "history_initialized": False,
    "history_columns": 3,
    "history_column_widths": [],
    "history_stats_widths": {},
    "history_groups": [],
    "active_history_group": "All",
    "metric_bar_labels": False,
    "tile_order": TILES.copy(),
    "hidden_tiles": [],
    "focused_tile": "metrics",
    "maximized_tile": None,
    "column_ratio": 0.50,
    "row_ratio": 0.54,
    "theme": "LUTEX Night",
    "theme_document": {},
    "ascii": False,
    "mouse": True,
    "colour_mode": "auto",
    "watches": [],
    "events": [],
}


def default_state_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "HWiNFOterm"
    return Path.home() / ".local" / "share" / "HWiNFOterm"


def config_path(state_dir: Path) -> Path:
    return state_dir / "config.json"


def _bounded_ratio(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return min(0.78, max(0.22, number))


def normalize(raw: Any) -> tuple[dict[str, Any], str | None]:
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_CONFIG), "Configuration root must be a JSON object"
    cfg = deepcopy(DEFAULT_CONFIG)
    error = None

    if raw.get("version") != 1:
        error = "Unrecognised config version; only recognised settings were recovered"
    for key in ("poll_interval", "stale_after", "history_seconds"):
        try:
            value = float(raw.get(key, cfg[key]))
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError
            if key == "poll_interval":
                cfg[key] = min(5.0, max(0.25, value))
            elif key == "stale_after":
                cfg[key] = min(120.0, max(1.0, value))
            else:
                cfg[key] = int(min(86_400, max(30, value)))
        except (TypeError, ValueError, OverflowError):
            if key in raw:
                error = error or f"Invalid {key}; its default is active"

    for key in ("pinned", "hidden_tiles", "watches", "events", "excluded_metrics", "history_keys", "cards_keys"):
        value = raw.get(key, cfg[key])
        if isinstance(value, list):
            cfg[key] = value[:512 if key in {"watches", "events"} else 4096]
        elif key in raw:
            error = error or f"Invalid {key}; its default is active"

    for key in ("ascii", "mouse"):
        if isinstance(raw.get(key), bool):
            cfg[key] = raw[key]
    for key in ("history_initialized", "cards_initialized", "chrome_hidden", "metric_bar_labels", "history_show_averages"):
        if isinstance(raw.get(key), bool):
            cfg[key] = raw[key]
    try:
        cfg["history_columns"] = min(6, max(1, int(raw.get("history_columns", cfg["history_columns"]))))
    except (TypeError, ValueError, OverflowError):
        if "history_columns" in raw:
            error = error or "Invalid history_columns; its default is active"
    try:
        cfg["cards_columns"] = min(6, max(1, int(raw.get("cards_columns", cfg["cards_columns"]))))
    except (TypeError, ValueError, OverflowError):
        if "cards_columns" in raw:
            error = error or "Invalid cards_columns; its default is active"
    raw_column_widths = raw.get("history_column_widths", [])
    if isinstance(raw_column_widths, list):
        column_widths = []
        for value in raw_column_widths[:6]:
            try:
                width = float(value)
                if math.isfinite(width) and width >= 0:
                    column_widths.append(min(100_000.0, width))
            except (TypeError, ValueError, OverflowError):
                continue
        cfg["history_column_widths"] = column_widths
    elif "history_column_widths" in raw:
        error = error or "Invalid history_column_widths; defaults are active"
    raw_cards_widths = raw.get("cards_column_widths", [])
    if isinstance(raw_cards_widths, list):
        cards_widths = []
        for value in raw_cards_widths[:6]:
            try:
                width = float(value)
                if math.isfinite(width) and width >= 0:
                    cards_widths.append(min(100_000.0, width))
            except (TypeError, ValueError, OverflowError):
                continue
        cfg["cards_column_widths"] = cards_widths
    elif "cards_column_widths" in raw:
        error = error or "Invalid cards_column_widths; defaults are active"
    raw_stats_widths = raw.get("history_stats_widths", {})
    if isinstance(raw_stats_widths, dict):
        stats_widths = {}
        for sensor_key, value in list(raw_stats_widths.items())[:4096]:
            if not isinstance(sensor_key, str) or not sensor_key or len(sensor_key) > 64 or isinstance(value, bool):
                continue
            try:
                width = int(value)
                # 11 and 8 were earlier minima; let those users reach the compact layout.
                if width in {8, 11}:
                    width = 7
                stats_widths[sensor_key] = min(20, max(7, width))
            except (TypeError, ValueError, OverflowError):
                continue
        cfg["history_stats_widths"] = stats_widths
    elif "history_stats_widths" in raw:
        error = error or "Invalid history_stats_widths; defaults are active"
    raw_groups = raw.get("history_groups", cfg["history_groups"])
    if isinstance(raw_groups, list):
        groups = []
        used_names: set[str] = set()
        for row in raw_groups[:32]:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            members = row.get("metrics", [])
            if not isinstance(name, str) or not isinstance(members, list):
                continue
            name = name.strip()[:32]
            folded = name.casefold()
            if not name or folded == "all" or folded in used_names:
                continue
            used_names.add(folded)
            clean_members = list(dict.fromkeys(key for key in members if isinstance(key, str) and len(key) <= 64))
            groups.append({"name": name, "metrics": clean_members[:4096]})
        cfg["history_groups"] = groups
    elif "history_groups" in raw:
        error = error or "Invalid history_groups; its default is active"
    active_group = raw.get("active_history_group", "All")
    if isinstance(active_group, str) and (active_group == "All" or any(group["name"] == active_group for group in cfg["history_groups"])):
        cfg["active_history_group"] = active_group
    colour_mode = raw.get("colour_mode")
    if isinstance(colour_mode, str) and colour_mode in {"auto", "truecolor", "256", "16", "mono"}:
        cfg["colour_mode"] = colour_mode
    elif "colour_mode" in raw:
        error = error or "Invalid colour_mode; its default is active"

    order = raw.get("tile_order")
    if isinstance(order, list):
        cfg["tile_order"] = list(dict.fromkeys([x for x in order if x in TILES] + TILES))[: len(TILES)]
    hidden = list(dict.fromkeys(x for x in cfg["hidden_tiles"] if x in TILES))
    cfg["hidden_tiles"] = hidden
    if len(hidden) == len(TILES):
        cfg["hidden_tiles"].remove("metrics")
    if raw.get("focused_tile") in TILES:
        cfg["focused_tile"] = raw["focused_tile"]
    if raw.get("maximized_tile") in TILES:
        cfg["maximized_tile"] = raw["maximized_tile"]
    cfg["column_ratio"] = _bounded_ratio(raw.get("column_ratio"), cfg["column_ratio"])
    cfg["row_ratio"] = _bounded_ratio(raw.get("row_ratio"), cfg["row_ratio"])
    if isinstance(raw.get("theme"), str) and len(raw["theme"]) <= 64:
        cfg["theme"] = raw["theme"]
    if isinstance(raw.get("theme_document"), dict):
        try:
            # JSON accepts NaN/Infinity by default; retaining them in an opaque
            # theme document would prevent every later settings save.
            json.dumps(raw["theme_document"], allow_nan=False)
            cfg["theme_document"] = deepcopy(raw["theme_document"])
        except (TypeError, ValueError, OverflowError, RecursionError):
            error = error or "Invalid theme_document; the default theme is active"
    if isinstance(raw.get("selected_sensor"), str) and len(raw["selected_sensor"]) <= 64:
        cfg["selected_sensor"] = raw["selected_sensor"]
    if isinstance(raw.get("filter_text"), str):
        cfg["filter_text"] = raw["filter_text"][:128]
    try:
        cfg["watch_index"] = max(0, min(255, int(raw.get("watch_index", 0))))
    except (TypeError, ValueError, OverflowError):
        pass

    cfg["pinned"] = list(dict.fromkeys(k for k in cfg["pinned"] if isinstance(k, str) and len(k) <= 64))
    for key in ("excluded_metrics", "history_keys"):
        cfg[key] = list(dict.fromkeys(k for k in cfg[key] if isinstance(k, str) and len(k) <= 64))
    # Cards are ordered slots: multiple cards may deliberately show one sensor.
    cfg["cards_keys"] = [k for k in cfg["cards_keys"] if isinstance(k, str) and k and len(k) <= 64][:256]
    raw_chart_rows = raw.get("history_chart_rows", {})
    if isinstance(raw_chart_rows, dict):
        chart_rows = {}
        for sensor_key, value in list(raw_chart_rows.items())[:4096]:
            if not isinstance(sensor_key, str) or not sensor_key or len(sensor_key) > 64 or isinstance(value, bool):
                continue
            try:
                chart_rows[sensor_key] = min(MAX_HISTORY_CHART_ROWS, max(1, int(value)))
            except (TypeError, ValueError, OverflowError):
                continue
        cfg["history_chart_rows"] = chart_rows
    elif "history_chart_rows" in raw:
        error = error or "Invalid history_chart_rows; its default is active"
    raw_chart_styles = raw.get("history_chart_styles", {})
    if isinstance(raw_chart_styles, dict):
        cfg["history_chart_styles"] = {
            sensor_key: value
            for sensor_key, value in list(raw_chart_styles.items())[:4096]
            if isinstance(sensor_key, str)
            and sensor_key
            and len(sensor_key) <= 64
            and value in HISTORY_CHART_STYLES
        }
    elif "history_chart_styles" in raw:
        error = error or "Invalid history_chart_styles; its default is active"
    clean_watches = []
    for row in cfg["watches"]:
        if not isinstance(row, dict):
            continue
        try:
            duration = float(row.get("duration", 0))
            if not math.isfinite(duration):
                continue
            item = {
                "id": str(row["id"])[:64],
                "sensor": str(row["sensor"])[:64],
                "label": str(row.get("label", row["sensor"]))[:128],
                "above": float(row["above"]),
                "below": float(row["below"]),
                "duration": min(3600.0, max(0.0, duration)),
                "active": row.get("active") is True,
                "since": None,
            }
            if all(math.isfinite(item[key]) for key in ("above", "below", "duration")) and item["above"] >= item["below"]:
                clean_watches.append(item)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    cfg["watches"] = clean_watches[:256]
    clean_events = []
    for event in cfg["events"]:
        if not isinstance(event, dict):
            continue
        try:
            value = float(event["value"])
            if not math.isfinite(value):
                continue
            clean_events.append({
                "time": str(event.get("time", ""))[:40],
                "watch": str(event.get("watch", ""))[:64],
                "sensor": str(event.get("sensor", ""))[:64],
                "label": str(event.get("label", ""))[:128],
                "state": str(event.get("state", ""))[:16],
                "value": value,
            })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    cfg["events"] = clean_events[:256]
    cfg["version"] = 1
    return cfg, error


def load_config(state_dir: Path) -> tuple[dict[str, Any], str | None]:
    path = config_path(state_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a JSON object")
        return normalize(raw)
    except FileNotFoundError:
        primary_error = None
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        primary_error = f"Could not read {path.name}: {exc}"

    # Read-only recovery: leave the damaged primary and last good backup intact
    # until the user next changes settings or quits normally.
    backup = path.with_suffix(".json.bak")
    try:
        raw = json.loads(backup.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a JSON object")
        cfg, warning = normalize(raw)
    except FileNotFoundError:
        return deepcopy(DEFAULT_CONFIG), primary_error
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        warning = f"Could not read backup {backup.name}: {exc}"
        return deepcopy(DEFAULT_CONFIG), f"{primary_error}; {warning}" if primary_error else warning
    message = f"Recovered configuration from backup {backup.name}"
    if warning:
        message += f"; {warning}"
    return cfg, message


def _replace_bytes(path: Path, document: bytes) -> None:
    """Atomically replace a settings file after flushing its complete contents."""
    fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def save_config(state_dir: Path, config: dict[str, Any]) -> None:
    document = json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = config_path(state_dir)
    try:
        previous = path.read_bytes()
        raw = json.loads(previous.decode("utf-8"))
        _, warning = normalize(raw)
        # Never replace a recoverable backup with a corrupt primary. Validate
        # opaque/unknown fields as well, without discarding them from the copy.
        json.dumps(raw, allow_nan=False)
        if warning is None:
            _replace_bytes(path.with_suffix(".json.bak"), previous)
    except (OSError, UnicodeError, TypeError, ValueError, RecursionError):
        pass
    _replace_bytes(path, document.encode("utf-8"))
