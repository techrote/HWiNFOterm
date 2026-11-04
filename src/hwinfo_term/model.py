"""Telemetry cache, bounded per-sensor history, and resilient source poller."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable

from .hwinfo import Reading, SharedMemoryError, Snapshot, read_snapshot


@dataclass(slots=True)
class SensorRecord:
    reading: Reading
    value: float | None
    observed_at: float
    history: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=1800))


@dataclass(slots=True)
class SourceStatus:
    state: str = "connecting"
    message: str = "Waiting for HWiNFO Shared Memory Support"
    since: float | None = None
    last_read: float | None = None
    last_change: float | None = None
    poll_time: int | None = None
    sample_count: int = 0


class TelemetryStore:
    def __init__(self, history_seconds: int = 900, stale_after: float = 5.0):
        self.lock = threading.RLock()
        self.records: dict[str, SensorRecord] = {}
        self.history_seconds = history_seconds
        self.stale_after = stale_after
        self.status = SourceStatus()
        self._last_content: tuple[tuple[str, float | None], ...] | None = None

    def ingest(self, snapshot: Snapshot, observed_at: float | None = None) -> bool:
        now = observed_at if observed_at is not None else time.time()
        content = tuple(sorted((key, reading.value) for key, reading in snapshot.readings.items()))
        with self.lock:
            changed = self.status.poll_time != snapshot.poll_time or content != self._last_content
            self.status.last_read = now
            if changed:
                self.status.last_change = now
                self.status.poll_time = snapshot.poll_time
                self.status.sample_count += 1
                self._last_content = content
                for key, reading in snapshot.readings.items():
                    record = self.records.get(key)
                    if record is None:
                        record = SensorRecord(reading=reading, value=reading.value, observed_at=now)
                        self.records[key] = record
                    else:
                        record.reading = reading
                        record.value = reading.value
                        record.observed_at = now
                    if reading.value is not None:
                        record.history.append((now, reading.value))
                    cutoff = now - self.history_seconds
                    while record.history and record.history[0][0] < cutoff:
                        record.history.popleft()
                self.status.state = "connected"
                self.status.message = "HWiNFO shared memory is updating"
                self.status.since = self.status.since or now
            self._refresh_stale_locked(now)
        return changed

    def source_error(self, message: str, now: float | None = None) -> None:
        at = now if now is not None else time.time()
        with self.lock:
            self.status.last_read = at
            if self.status.last_change is None:
                self.status.state = "unavailable"
                self.status.message = message
            else:
                self._refresh_stale_locked(at)
                if self.status.state != "stale":
                    self.status.state = "unavailable"
                    self.status.message = message

    def _refresh_stale_locked(self, now: float) -> None:
        if self.status.last_change is None:
            return
        age = now - self.status.last_change
        if age >= self.stale_after:
            self.status.state = "stale"
            self.status.message = "HWiNFO readings have stopped updating"
        elif self.status.state in {"stale", "unavailable"}:
            self.status.state = "connected"
            self.status.message = "HWiNFO shared memory is updating"

    def quality(self, record: SensorRecord, now: float | None = None) -> str:
        current = now if now is not None else time.time()
        if record.value is None:
            return "missing"
        if current - record.observed_at >= self.stale_after:
            return "stale"
        return "good"

    def snapshot(self) -> tuple[dict[str, SensorRecord], SourceStatus]:
        now = time.time()
        with self.lock:
            self._refresh_stale_locked(now)
            rows = {key: replace(record, history=deque()) for key, record in self.records.items()}
            return rows, replace(self.status)

    def history_for(self, key: str) -> list[tuple[float, float]]:
        with self.lock:
            record = self.records.get(key)
            return list(record.history) if record else []

    def set_limits(self, history_seconds: int, stale_after: float) -> None:
        with self.lock:
            self.history_seconds = max(30, int(history_seconds))
            self.stale_after = max(1.0, float(stale_after))
            cutoff = time.time() - self.history_seconds
            for record in self.records.values():
                while record.history and record.history[0][0] < cutoff:
                    record.history.popleft()


class ReaderThread:
    def __init__(self, store: TelemetryStore, interval: float, on_update: Callable[[], None] | None = None):
        self.store = store
        self.interval = min(5.0, max(0.25, float(interval)))
        self.on_update = on_update
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="hwinfo-reader")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _run(self) -> None:
        failures = 0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                snapshot = read_snapshot()
                self.store.ingest(snapshot)
                failures = 0
                if self.on_update:
                    self.on_update()
            except SharedMemoryError as exc:
                failures += 1
                self.store.source_error(str(exc))
            except (OSError, ValueError, OverflowError) as exc:
                failures += 1
                self.store.source_error(f"Shared-memory read failed: {type(exc).__name__}")
            delay = min(15.0, self.interval * (2 ** min(failures, 4)))
            self.stop_event.wait(max(0.05, delay - (time.monotonic() - started)))


def demo_snapshot(sequence: int, now: float | None = None) -> Snapshot:
    """Clearly labelled synthetic data, available only through --demo."""
    at = now if now is not None else time.time()
    cpu = 44 + 16 * math.sin(at / 5.3)
    gpu = 31 + 24 * math.sin(at / 7.1 + 1.2)
    values = [
        ("demo:cpu_load", "CPU", "Total CPU Utility", "%", "usage", cpu),
        ("demo:cpu_temp", "CPU", "CPU Package", "°C", "temperature", 54 + 7 * math.sin(at / 9)),
        ("demo:gpu_load", "GPU", "GPU Core Load", "%", "usage", gpu),
        ("demo:gpu_temp", "GPU", "GPU Temperature", "°C", "temperature", 48 + 8 * math.sin(at / 8)),
        ("demo:memory", "Memory", "Physical Memory Load", "%", "usage", 57 + 2 * math.sin(at / 13)),
        ("demo:fan", "Cooling", "CPU Fan", "RPM", "fan", 920 + 80 * math.sin(at / 4)),
    ]
    readings: dict[str, Reading] = {}
    for key, sensor, label, unit, kind, value in values:
        readings[key] = Reading(
            key=key,
            sensor_id=0,
            sensor_instance=0,
            reading_id=sequence,
            sensor=sensor,
            label=label,
            unit=unit,
            kind=kind,
            value=round(value, 2),
            minimum=None,
            maximum=None,
            average=None,
        )
    return Snapshot(poll_time=sequence, readings=readings)


class DemoThread:
    def __init__(self, store: TelemetryStore, interval: float, on_update: Callable[[], None] | None = None):
        self.store = store
        self.interval = min(5.0, max(0.25, float(interval)))
        self.on_update = on_update
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="hwinfo-demo")

    def start(self) -> None:
        with self.store.lock:
            self.store.status.state = "demo"
            self.store.status.message = "SIMULATED DATA · explicit demo mode"
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _run(self) -> None:
        sequence = 0
        while not self.stop_event.is_set():
            sequence += 1
            self.store.ingest(demo_snapshot(sequence))
            with self.store.lock:
                self.store.status.state = "demo"
                self.store.status.message = "SIMULATED DATA · explicit demo mode"
            if self.on_update:
                self.on_update()
            self.stop_event.wait(self.interval)
