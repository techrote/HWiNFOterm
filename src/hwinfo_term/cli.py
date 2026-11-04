"""Command-line entry points for the HWiNFOterm TUI and one-shot reader."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from . import __version__
from .config import default_state_dir, load_config
from .hwinfo import SharedMemoryError, read_snapshot
from .model import demo_snapshot
from .terminal import _safe_text
from .tui import TUIApp


def _snapshot(args: argparse.Namespace) -> int:
    try:
        snap = demo_snapshot(1) if args.demo else read_snapshot()
    except SharedMemoryError as exc:
        print(f"HWiNFOterm: {exc}", file=sys.stderr)
        return 2
    readings = sorted(snap.readings.values(), key=lambda item: (item.sensor.casefold(), item.label.casefold()))
    if args.json:
        document = {
            "provider": "simulated-demo" if args.demo else "HWiNFO-SM2",
            "poll_time": snap.poll_time,
            "observed_at": time.time(),
            "readings": [
                {
                    "key": item.key,
                    "sensor": item.sensor,
                    "label": item.label,
                    "unit": item.unit,
                    "kind": item.kind,
                    "value": item.value,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "average": item.average,
                }
                for item in readings
            ],
        }
        # ASCII escapes keep JSON lossless when Windows redirects stdout through
        # a legacy code page. Console text modes still display Unicode labels.
        print(json.dumps(document, ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    if args.scan:
        print(f"HWiNFOterm sensor catalog · {len(readings)} readings · poll token {snap.poll_time}")
        print("KEY                                 SENSOR                         READING                         VALUE")
        for item in readings:
            value = "—" if item.value is None else f"{item.value:g} {_safe_text(item.unit)}".rstrip()
            print(f"{_safe_text(item.key):35.35} {_safe_text(item.sensor):30.30} {_safe_text(item.label):31.31} {value}")
        return 0
    for item in readings:
        value = "—" if item.value is None else f"{item.value:g} {_safe_text(item.unit)}".rstrip()
        print(f"{_safe_text(item.sensor)} · {_safe_text(item.label)}: {value} [{_safe_text(item.key)}]")
    return 0


def _finite_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite number") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be a finite number")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwinfo-term",
        description="A Windows Terminal dashboard for HWiNFO shared-memory sensors.",
    )
    parser.add_argument("--version", action="version", version=f"HWiNFOterm {__version__}")
    parser.add_argument("--state-dir", type=Path, default=None, help="settings and watch storage directory")
    parser.add_argument("--poll-interval", type=_finite_float, default=None, help="provider poll interval in seconds (0.25–5)")
    parser.add_argument("--stale-after", type=_finite_float, default=None, help="mark readings stale after this many seconds")
    parser.add_argument("--history-seconds", type=int, default=None, help="bounded chart history window")
    parser.add_argument("--color", choices=("auto", "truecolor", "256", "16", "mono"), default=None, help="terminal colour capability")
    parser.add_argument("--ascii", action="store_true", help="use ASCII borders, graph and markers")
    parser.add_argument("--no-mouse", action="store_true", help="disable terminal mouse reporting")
    parser.add_argument("--demo", action="store_true", help="run an explicitly labelled synthetic-data demo")
    parser.add_argument("--scan", action="store_true", help="print the current sensor catalog and exit")
    parser.add_argument("--once", action="store_true", help="read one snapshot and exit")
    parser.add_argument("--json", action="store_true", help="emit the one-shot result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.scan or args.once:
        return _snapshot(args)
    if args.json:
        parser.error("--json requires --once or --scan")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("HWiNFOterm needs an interactive Windows Terminal session. Use --once or --scan for non-interactive reads.", file=sys.stderr)
        return 2

    state_dir = args.state_dir or (default_state_dir() / "demo" if args.demo else default_state_dir())
    config, config_error = load_config(state_dir)
    if args.poll_interval is not None:
        config["poll_interval"] = min(5.0, max(0.25, args.poll_interval))
    if args.stale_after is not None:
        config["stale_after"] = min(120.0, max(1.0, args.stale_after))
    if args.history_seconds is not None:
        config["history_seconds"] = min(86_400, max(30, args.history_seconds))
    if args.color is not None:
        config["colour_mode"] = args.color
    app = TUIApp(
        state_dir=state_dir,
        config=config,
        config_error=config_error,
        demo=args.demo,
        ascii_only=True if args.ascii else None,
        mouse=False if args.no_mouse else None,
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
