"""Read the HWiNFO SM2 shared-memory mapping without modifying the provider."""

from __future__ import annotations

import ctypes
import math
import os
import struct
from dataclasses import dataclass


MAPPING_NAME = "Global\\HWiNFO_SENS_SM2"
MUTEX_NAME = "Global\\HWiNFO_SM2_MUTEX"
HEADER_PACKED = struct.Struct("<IIIqIIIIII")
HEADER_ALIGNED = struct.Struct("<III4xqIIIIII")
HEADER_SIZE = HEADER_PACKED.size
HEADER_MAX_SIZE = max(HEADER_PACKED.size, HEADER_ALIGNED.size)
READING_PACKED = struct.Struct("<III128s128s16sdddd")
READING_ALIGNED = struct.Struct("<III128s128s16s4xdddd")
READING_FORMATS = (READING_PACKED, READING_ALIGNED)
MAX_MAPPING_BYTES = 64 * 1024 * 1024
MAX_SENSOR_COUNT = 10_000
MAX_READING_COUNT = 100_000
SENSOR_MIN_SIZE = 264
READING_MIN_SIZE = READING_PACKED.size

READING_KINDS = {
    0: "none",
    1: "temperature",
    2: "voltage",
    3: "fan",
    4: "current",
    5: "power",
    6: "clock",
    7: "usage",
    8: "other",
}


class SharedMemoryError(RuntimeError):
    """A recoverable HWiNFO connection or mapping error."""


@dataclass(frozen=True, slots=True)
class Reading:
    key: str
    sensor_id: int
    sensor_instance: int
    reading_id: int
    sensor: str
    label: str
    unit: str
    kind: str
    value: float | None
    minimum: float | None
    maximum: float | None
    average: float | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    poll_time: int
    readings: dict[str, Reading]


def _text(data: bytes, start: int, length: int, encoding: str | None = None) -> str:
    raw = data[start : start + length].split(b"\0", 1)[0]
    if encoding is None:
        encoding = "mbcs" if os.name == "nt" else "latin1"
    return raw.decode(encoding, errors="replace").strip()


def _layout(data: bytes) -> tuple[int, int, int, int, int, int, int, int]:
    if len(data) < HEADER_SIZE:
        raise SharedMemoryError("HWiNFO shared-memory header is incomplete")
    if data[:4] != b"HWiS":
        raise SharedMemoryError("HWiNFO shared memory is inactive")
    version = struct.unpack_from("<I", data, 4)[0]
    if version != 2:
        raise SharedMemoryError(f"Unsupported HWiNFO shared-memory version {version}; expected SM2 version 2")

    candidates = []
    for header in (HEADER_PACKED, HEADER_ALIGNED):
        if len(data) < header.size:
            continue
        signature, candidate_version, revision, poll_time, sensor_offset, sensor_size, sensor_count, reading_offset, reading_size, reading_count = header.unpack_from(data)
        del signature, revision
        if candidate_version != 2:
            continue
        if sensor_count > MAX_SENSOR_COUNT or reading_count > MAX_READING_COUNT:
            continue
        if sensor_size < SENSOR_MIN_SIZE or reading_size < READING_MIN_SIZE:
            continue
        if sensor_offset < header.size or reading_offset < header.size:
            continue

        sensor_end = sensor_offset + sensor_size * sensor_count
        reading_end = reading_offset + reading_size * reading_count
        if reading_offset < sensor_end or reading_end > MAX_MAPPING_BYTES:
            continue
        candidates.append((reading_offset != sensor_end, header.size, (
            poll_time, sensor_offset, sensor_size, sensor_count,
            reading_offset, reading_size, reading_count, reading_end,
        )))

    if not candidates:
        raise SharedMemoryError("HWiNFO reported invalid or unsupported section descriptors")
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    best_adjacency = candidates[0][0]
    best_candidates = [candidate for candidate in candidates if candidate[0] == best_adjacency]
    if len(best_candidates) > 1 and best_candidates[0][2] != best_candidates[1][2]:
        raise SharedMemoryError("HWiNFO shared-memory header layout is ambiguous")
    return best_candidates[0][2]


def _plausible_sample(value: float) -> bool:
    return math.isfinite(value) and (value == 0.0 or 1e-150 <= abs(value) <= 1e150)


def _reading_format(data: bytes, offset: int, stride: int, count: int) -> struct.Struct:
    """Choose the known numeric prefix using row size and real-value evidence."""
    formats = [candidate for candidate in READING_FORMATS if candidate.size <= stride]
    if not formats:
        raise SharedMemoryError("HWiNFO reading rows are smaller than any supported layout")
    if count == 0:
        return READING_ALIGNED if stride in {READING_ALIGNED.size, READING_ALIGNED.size + 144} else READING_PACKED
    scores = []
    sample_count = min(count, 64)
    for candidate in formats:
        offset_values = candidate.size - 4 * 8
        plausible = nonzero = 0
        for index in range(sample_count):
            base = offset + index * stride + offset_values
            for value_index in range(4):
                value = struct.unpack_from("<d", data, base + value_index * 8)[0]
                if _plausible_sample(value):
                    plausible += 1
                    nonzero += value != 0.0
        scores.append((candidate, plausible, nonzero))

    expected_size = {
        READING_PACKED.size: READING_PACKED.size,
        READING_ALIGNED.size: READING_ALIGNED.size,
        READING_PACKED.size + 144: READING_PACKED.size,
        READING_ALIGNED.size + 144: READING_ALIGNED.size,
    }.get(stride)
    best_score = max((nonzero, plausible) for _, plausible, nonzero in scores)
    best = [candidate for candidate, plausible, nonzero in scores if (nonzero, plausible) == best_score]
    if expected_size is not None:
        expected = next((candidate for candidate in best if candidate.size == expected_size), None)
        if expected is not None:
            return expected
    if len(best) == 1:
        return best[0]
    raise SharedMemoryError("HWiNFO reading layout could not be identified safely")


def parse_mapping(data: bytes) -> Snapshot:
    """Parse one copied SM2 mapping. Extra fields in future strides are ignored."""
    poll_time, sensor_offset, sensor_size, sensor_count, reading_offset, reading_size, reading_count, required_size = _layout(data)
    if len(data) < required_size:
        raise SharedMemoryError("HWiNFO shared-memory sections are incomplete")

    reading_format = _reading_format(data, reading_offset, reading_size, reading_count)
    sensors: list[tuple[int, int, str]] = []
    for index in range(sensor_count):
        base = sensor_offset + index * sensor_size
        sensor_id, instance = struct.unpack_from("<II", data, base)
        utf8_name = _text(data, base + SENSOR_MIN_SIZE, 128, "utf-8") if sensor_size >= SENSOR_MIN_SIZE + 128 else ""
        name = utf8_name or _text(data, base + 136, 128) or _text(data, base + 8, 128)
        sensors.append((sensor_id, instance, name or f"Sensor {sensor_id:08X}"))

    readings: dict[str, Reading] = {}
    for index in range(reading_count):
        base = reading_offset + index * reading_size
        reading_type, sensor_index, reading_id = struct.unpack_from("<III", data, base)
        if sensor_index >= len(sensors):
            raise SharedMemoryError("HWiNFO reading refers to an unknown sensor")
        sensor_id, instance, sensor_name = sensors[sensor_index]
        key = f"{sensor_id:08x}:{instance}:{reading_id:08x}"
        utf8_prefix = {460: READING_PACKED.size, 464: READING_ALIGNED.size}.get(reading_size)
        has_utf8_fields = utf8_prefix == reading_format.size
        label = _text(data, base + reading_format.size, 128, "utf-8") if has_utf8_fields else ""
        unit = _text(data, base + reading_format.size + 128, 16, "utf-8") if has_utf8_fields else ""
        label = label or _text(data, base + 140, 128) or _text(data, base + 12, 128) or key
        unit = unit or _text(data, base + 268, 16)
        value, minimum, maximum, average = struct.unpack_from("<dddd", data, base + reading_format.size - 32)
        readings[key] = Reading(
            key=key,
            sensor_id=sensor_id,
            sensor_instance=instance,
            reading_id=reading_id,
            sensor=sensor_name,
            label=label,
            unit=unit,
            kind=READING_KINDS.get(reading_type, "other"),
            value=value if math.isfinite(value) else None,
            minimum=minimum if math.isfinite(minimum) else None,
            maximum=maximum if math.isfinite(maximum) else None,
            average=average if math.isfinite(average) else None,
        )
    return Snapshot(poll_time=poll_time, readings=readings)


def read_snapshot(timeout_ms: int = 300) -> Snapshot:
    """Copy one consistent SM2 snapshot while holding HWiNFO's named mutex."""
    if os.name != "nt":
        raise SharedMemoryError("HWiNFO shared memory is available only on Windows")

    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenMutexW.restype = w.HANDLE
    kernel.OpenFileMappingW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenFileMappingW.restype = w.HANDLE
    kernel.MapViewOfFile.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.c_size_t]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.UnmapViewOfFile.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.ReleaseMutex.argtypes = [w.HANDLE]
    kernel.ReleaseMutex.restype = w.BOOL

    mutex = kernel.OpenMutexW(0x00100001, False, MUTEX_NAME)  # SYNCHRONIZE | MUTEX_MODIFY_STATE
    if not mutex:
        raise SharedMemoryError("Open HWiNFO Sensors and enable Shared Memory Support")

    mapping = None
    view = None
    acquired = False
    try:
        wait_result = kernel.WaitForSingleObject(mutex, max(0, min(timeout_ms, 5000)))
        if wait_result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise SharedMemoryError("Timed out waiting for a consistent HWiNFO snapshot")
        acquired = True

        mapping = kernel.OpenFileMappingW(0x0004, False, MAPPING_NAME)  # FILE_MAP_READ
        if not mapping:
            raise SharedMemoryError("HWiNFO shared-memory mapping is unavailable")

        header_map_size = HEADER_MAX_SIZE
        view = kernel.MapViewOfFile(mapping, 0x0004, 0, 0, header_map_size)
        if not view:
            header_map_size = HEADER_SIZE
            view = kernel.MapViewOfFile(mapping, 0x0004, 0, 0, header_map_size)
        if not view:
            raise SharedMemoryError("Could not map the HWiNFO header")
        header_copy = ctypes.string_at(view, header_map_size)
        required_size = _layout(header_copy)[-1]
        kernel.UnmapViewOfFile(view)
        view = None

        view = kernel.MapViewOfFile(mapping, 0x0004, 0, 0, required_size)
        if not view:
            raise SharedMemoryError("Could not map HWiNFO sensor readings")
        copied = ctypes.string_at(view, required_size)
        return parse_mapping(copied)
    except OSError as exc:
        raise SharedMemoryError(f"Windows shared-memory call failed: {exc}") from exc
    finally:
        if view:
            kernel.UnmapViewOfFile(view)
        if mapping:
            kernel.CloseHandle(mapping)
        if acquired:
            kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)
