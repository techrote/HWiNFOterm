"""Regressions for telemetry integrity and shared-memory lifecycle handling."""

import struct
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from hwinfo_term import hwinfo
from hwinfo_term.hwinfo import (
    HEADER_ALIGNED,
    HEADER_PACKED,
    READING_ALIGNED,
    READING_PACKED,
    Reading,
    SharedMemoryError,
    Snapshot,
    parse_mapping,
)
from hwinfo_term.model import TelemetryStore


def mapping_bytes(header=HEADER_ALIGNED, reading_format=READING_ALIGNED, *, values=None, extended=False):
    """Create exactly one sensor/reading with independently controlled numeric data."""
    sensor_size = 392 if extended else 264
    reading_size = reading_format.size + (144 if extended else 0)
    reading_offset = header.size + sensor_size
    data = bytearray(reading_offset + reading_size)
    header.pack_into(data, 0, int.from_bytes(b"HWiS", "little"), 2, 1, 123,
                     header.size, sensor_size, 1, reading_offset, reading_size, 1)
    struct.pack_into("<II", data, header.size, 1, 0)
    data[header.size + 8:header.size + 11] = b"CPU"
    reading_format.pack_into(data, reading_offset, 1, 0, 2, b"Temperature", b"Temperature", b"C",
                             *(values if values is not None else (73.25, 31.0, 96.5, 58.75)))
    if extended:
        data[reading_offset + reading_format.size:reading_offset + reading_format.size + 4] = b"Temp"
    return bytes(data)


def temperature(value=70.0):
    return Reading("00000001:0:00000002", 1, 0, 2, "CPU", "Temperature", "C", "temperature",
                   value, 30.0, 90.0, 60.0)


class ReaderIntegrityTests(unittest.TestCase):
    def test_nonfinite_known_layout_values_never_choose_another_prefix(self):
        for header in (HEADER_PACKED, HEADER_ALIGNED):
            for reading_format in (READING_PACKED, READING_ALIGNED):
                for extended in (False, True):
                    with self.subTest(header=header.size, reading=reading_format.size, extended=extended):
                        snap = parse_mapping(mapping_bytes(header, reading_format, extended=extended,
                                                           values=(float("nan"),) * 4))
                        reading = next(iter(snap.readings.values()))
                        self.assertEqual((reading.value, reading.minimum, reading.maximum, reading.average),
                                         (None, None, None, None))

    def test_known_layout_preserves_zero_and_nonfinite_statistics(self):
        for reading_format in (READING_PACKED, READING_ALIGNED):
            with self.subTest(reading=reading_format.size):
                snap = parse_mapping(mapping_bytes(reading_format=reading_format,
                                                   values=(0.0, float("nan"), float("inf"), -float("inf"))))
                reading = next(iter(snap.readings.values()))
                self.assertEqual((reading.value, reading.minimum, reading.maximum, reading.average),
                                 (0.0, None, None, None))

    def test_unknown_stride_is_rejected_without_guessing_from_numbers(self):
        data = bytearray(mapping_bytes())
        # Aligned header's reading stride is at byte 40.
        struct.pack_into("<I", data, 40, READING_ALIGNED.size + 8)
        data.extend(b"\0" * 8)
        with self.assertRaisesRegex(SharedMemoryError, "Unsupported.*reading.*size"):
            parse_mapping(bytes(data))

    def test_duplicate_stable_identity_is_rejected(self):
        data = bytearray(mapping_bytes())
        reading_offset = HEADER_ALIGNED.size + 264
        data.extend(data[reading_offset:])
        struct.pack_into("<I", data, 44, 2)
        with self.assertRaisesRegex(SharedMemoryError, "duplicate.*identity"):
            parse_mapping(bytes(data))

    def test_truncated_sections_are_rejected(self):
        with self.assertRaisesRegex(SharedMemoryError, "incomplete"):
            parse_mapping(mapping_bytes()[:-1])

    def test_unknown_sensor_reference_is_rejected(self):
        data = bytearray(mapping_bytes())
        struct.pack_into("<I", data, HEADER_ALIGNED.size + 264 + 4, 1)
        with self.assertRaisesRegex(SharedMemoryError, "unknown sensor"):
            parse_mapping(bytes(data))


class TelemetryAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.store = TelemetryStore(history_seconds=60, stale_after=5.0)
        self.reading = temperature()
        self.snapshot = Snapshot(1, {self.reading.key: self.reading})
        self.store.ingest(self.snapshot, observed_at=100.0)

    def test_snapshot_does_not_clear_provider_failure(self):
        self.store.source_error("mapping unavailable", now=101.0)
        for now in (101.0, 106.0):
            with self.subTest(now=now), patch("hwinfo_term.model.time.time", return_value=now):
                records, status = self.store.snapshot()
                self.assertEqual(status.state, "unavailable")
                self.assertEqual(status.message, "mapping unavailable")
                self.assertEqual(status.last_read, 100.0)
                self.assertEqual(self.store.quality(records[self.reading.key]), "unavailable")

    def test_successful_read_restores_availability_but_not_old_freshness(self):
        self.store.source_error("mapping unavailable", now=101.0)
        self.assertFalse(self.store.ingest(self.snapshot, observed_at=102.0))
        self.assertEqual(self.store.status.state, "connected")
        self.assertEqual(self.store.status.sample_count, 1)
        self.store.source_error("mapping unavailable", now=103.0)
        self.assertFalse(self.store.ingest(self.snapshot, observed_at=106.0))
        self.assertEqual(self.store.status.state, "stale")
        self.assertEqual(self.store.quality(self.store.records[self.reading.key], now=106.0), "stale")
        self.assertTrue(self.store.ingest(Snapshot(2, self.snapshot.readings), observed_at=107.0))
        self.assertEqual(self.store.status.state, "connected")
        self.assertEqual(self.store.quality(self.store.records[self.reading.key], now=107.0), "good")

    def test_interruption_count_survives_recovery_between_ui_snapshots(self):
        with patch("hwinfo_term.model.time.time", return_value=100.0):
            _, before = self.store.snapshot()
        self.assertEqual(before.interruption_count, 0)

        # A UI can miss both the failure and recovery during a single frame.
        self.store.source_error("mapping unavailable", now=101.0)
        self.store.ingest(Snapshot(2, self.snapshot.readings), observed_at=102.0)
        with patch("hwinfo_term.model.time.time", return_value=102.0):
            _, recovered = self.store.snapshot()
        self.assertEqual(recovered.state, "connected")
        self.assertEqual(recovered.interruption_count, 1)
        self.assertEqual(before.interruption_count, 0)

        # Successful reads, including identical snapshots, never erase evidence
        # of the interruption. Subsequent failures monotonically advance it.
        self.store.ingest(Snapshot(2, self.snapshot.readings), observed_at=103.0)
        self.assertEqual(self.store.status.interruption_count, 1)
        self.store.source_error("mutex unavailable", now=103.5)
        self.store.ingest(Snapshot(3, self.snapshot.readings), observed_at=104.0)
        with patch("hwinfo_term.model.time.time", return_value=104.0):
            _, recovered_again = self.store.snapshot()
        self.assertEqual(recovered_again.state, "connected")
        self.assertEqual(recovered_again.interruption_count, 2)

    def test_removed_sensor_is_missing_immediately_and_can_reappear(self):
        self.store.ingest(Snapshot(2, {}), observed_at=101.0)
        record = self.store.records[self.reading.key]
        self.assertEqual(self.store.quality(record, now=101.0), "missing")
        self.assertIsNone(record.value)
        self.assertIsNone(record.reading.value)
        self.assertEqual(self.store.history_for(self.reading.key), [(100.0, 70.0)])
        self.store.ingest(Snapshot(3, {self.reading.key: temperature(71.0)}), observed_at=102.0)
        self.assertEqual(self.store.quality(record, now=102.0), "good")
        self.assertEqual(record.value, 71.0)
        self.assertEqual(self.store.history_for(self.reading.key), [(100.0, 70.0), (102.0, 71.0)])

    def test_metadata_updates_without_changing_current_value_are_retained(self):
        updated = replace(self.reading, label="Renamed temperature", maximum=95.0)
        self.assertTrue(self.store.ingest(Snapshot(1, {updated.key: updated}), observed_at=101.0))
        self.assertEqual(self.store.records[updated.key].reading.label, "Renamed temperature")
        self.assertEqual(self.store.records[updated.key].reading.maximum, 95.0)

    def test_unchanged_poll_does_not_extend_history_or_freshness(self):
        self.assertFalse(self.store.ingest(self.snapshot, observed_at=104.0))
        self.assertEqual(self.store.status.last_change, 100.0)
        self.assertEqual(self.store.history_for(self.reading.key), [(100.0, 70.0)])
        with patch("hwinfo_term.model.time.time", return_value=105.0):
            records, status = self.store.snapshot()
            self.assertEqual(status.state, "stale")
            self.assertEqual(self.store.quality(records[self.reading.key]), "stale")


class WindowsCallLifecycleTests(unittest.TestCase):
    """API contract checks use mocks; separate Windows tests exercise real handles."""

    def _kernel(self, events):
        kernel = Mock()
        kernel.OpenMutexW.return_value = 11
        kernel.OpenFileMappingW.return_value = 22
        kernel.WaitForSingleObject.return_value = 0
        kernel.MapViewOfFile.side_effect = [33, 44]
        kernel.UnmapViewOfFile.side_effect = lambda view: events.append(("unmap", view)) or 1
        kernel.ReleaseMutex.side_effect = lambda handle: events.append(("release", handle)) or 1
        kernel.CloseHandle.side_effect = lambda handle: events.append(("close", handle)) or 1
        return kernel

    def test_native_resources_are_released_before_python_parsing(self):
        events = []
        kernel = self._kernel(events)
        data = mapping_bytes()
        expected = Snapshot(123, {})
        def parse(copied):
            self.assertEqual(copied, data)
            events.append(("parse", None))
            return expected
        with patch.object(hwinfo, "os", types.SimpleNamespace(name="nt")), \
                patch.object(hwinfo.ctypes, "WinDLL", return_value=kernel, create=True), \
                patch.object(hwinfo.ctypes, "string_at", side_effect=[data[:hwinfo.HEADER_MAX_SIZE], data]), \
                patch.object(hwinfo, "parse_mapping", side_effect=parse):
            self.assertIs(hwinfo.read_snapshot(), expected)
        self.assertLess(events.index(("release", 11)), events.index(("parse", None)))
        self.assertLess(events.index(("close", 22)), events.index(("parse", None)))
        self.assertEqual(kernel.ReleaseMutex.call_count, 1)
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_wait_failure_is_distinguished_from_timeout(self):
        events = []
        kernel = self._kernel(events)
        kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch.object(hwinfo, "os", types.SimpleNamespace(name="nt")), \
                patch.object(hwinfo.ctypes, "WinDLL", return_value=kernel, create=True), \
                patch.object(hwinfo.ctypes, "get_last_error", return_value=6, create=True):
            with self.assertRaisesRegex(SharedMemoryError, "wait failed.*6"):
                hwinfo.read_snapshot()
        kernel.ReleaseMutex.assert_not_called()
        kernel.OpenFileMappingW.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(11)

    def test_timeout_does_not_release_unowned_mutex(self):
        events = []
        kernel = self._kernel(events)
        kernel.WaitForSingleObject.return_value = 0x102
        with patch.object(hwinfo, "os", types.SimpleNamespace(name="nt")), \
                patch.object(hwinfo.ctypes, "WinDLL", return_value=kernel, create=True):
            with self.assertRaisesRegex(SharedMemoryError, "Timed out"):
                hwinfo.read_snapshot()
        kernel.ReleaseMutex.assert_not_called()
        kernel.OpenFileMappingW.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(11)


if __name__ == "__main__":
    unittest.main()
