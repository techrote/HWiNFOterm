"""Native Windows shared-memory integration, using isolated local test objects.

These tests exercise real Win32 handles and synchronization. They do not claim
compatibility with a live HWiNFO installation or its mapping/schema versions.
"""

import ctypes
import mmap
import os
import struct
import threading
import unittest
import uuid
from unittest.mock import patch

from hwinfo_term import hwinfo
from hwinfo_term.hwinfo import SharedMemoryError
from test_reader_reliability import mapping_bytes


@unittest.skipUnless(os.name == "nt", "requires actual Win32 named mappings and mutexes")
class WindowsSharedMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        from ctypes import wintypes as w

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        self.kernel.CreateMutexW.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.WaitForSingleObject.restype = w.DWORD
        self.kernel.ReleaseMutex.argtypes = [w.HANDLE]
        self.kernel.ReleaseMutex.restype = w.BOOL
        prefix = "Local\\HWiNFOterm-test-" + uuid.uuid4().hex
        self.mapping_name = prefix + "-mapping"
        self.mutex_name = prefix + "-mutex"
        self.mutex = self.kernel.CreateMutexW(None, False, self.mutex_name)
        if not self.mutex:
            self.fail(f"CreateMutexW failed: {ctypes.get_last_error()}")
        self.addCleanup(self.kernel.CloseHandle, self.mutex)
        self.names = patch.multiple(hwinfo, MAPPING_NAME=self.mapping_name, MUTEX_NAME=self.mutex_name)
        self.names.start()
        self.addCleanup(self.names.stop)

    def create_mapping(self, data):
        mapping = mmap.mmap(-1, len(data), tagname=self.mapping_name, access=mmap.ACCESS_WRITE)
        mapping.write(data)
        self.addCleanup(mapping.close)
        return mapping

    def assert_mutex_available_to_another_thread(self):
        # A same-thread wait is recursive and would miss an unreleased mutex.
        results = []
        def acquire():
            result = self.kernel.WaitForSingleObject(self.mutex, 500)
            results.append(result)
            if result in (0, 0x80):
                self.kernel.ReleaseMutex(self.mutex)
        thread = threading.Thread(target=acquire, daemon=True)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "mutex probe did not finish")
        self.assertEqual(results, [0], "reader did not release its mutex ownership")

    def test_real_mapping_can_be_read_and_all_mutex_ownership_is_released(self):
        data = mapping_bytes()
        mapping = self.create_mapping(data)
        for _ in range(3):
            snapshot = hwinfo.read_snapshot()
            reading = next(iter(snapshot.readings.values()))
            self.assertEqual(snapshot.poll_time, 123)
            self.assertEqual(reading.value, 73.25)
            self.assertEqual(reading.sensor, "CPU")
            self.assertEqual(reading.label, "Temperature")
        self.assertEqual(mapping[:], data, "reader must not modify provider memory")
        self.assert_mutex_available_to_another_thread()

    def test_missing_mapping_releases_mutex(self):
        with self.assertRaisesRegex(SharedMemoryError, "mapping is unavailable"):
            hwinfo.read_snapshot()
        self.assert_mutex_available_to_another_thread()
        self.create_mapping(mapping_bytes())
        self.assertEqual(hwinfo.read_snapshot().poll_time, 123)

    def test_invalid_mapping_bounds_are_rejected_and_mutex_released(self):
        data = bytearray(mapping_bytes())
        # Aligned header: an out-of-bounds section end must fail before mapping it.
        struct.pack_into("<I", data, 36, hwinfo.MAX_MAPPING_BYTES)
        self.create_mapping(data)
        with self.assertRaisesRegex(SharedMemoryError, "invalid or unsupported section descriptors"):
            hwinfo.read_snapshot()
        self.assert_mutex_available_to_another_thread()

    def test_parse_failure_releases_mutex_and_allows_recovery(self):
        data = bytearray(mapping_bytes())
        struct.pack_into("<I", data, 40, hwinfo.READING_ALIGNED.size + 8)
        data.extend(b"\0" * 8)
        mapping = self.create_mapping(data)
        with self.assertRaisesRegex(SharedMemoryError, "Unsupported.*reading.*size"):
            hwinfo.read_snapshot()
        self.assert_mutex_available_to_another_thread()
        mapping.seek(0)
        mapping.write(mapping_bytes())
        self.assertEqual(hwinfo.read_snapshot().poll_time, 123)

    def test_contended_mutex_returns_timeout_without_stealing_ownership(self):
        self.create_mapping(mapping_bytes())
        acquired = threading.Event()
        finish = threading.Event()
        results = []
        def hold_mutex():
            result = self.kernel.WaitForSingleObject(self.mutex, 500)
            results.append(result)
            acquired.set()
            if result in (0, 0x80):
                finish.wait()
                self.kernel.ReleaseMutex(self.mutex)
        thread = threading.Thread(target=hold_mutex, daemon=True)
        thread.start()
        try:
            self.assertTrue(acquired.wait(timeout=2))
            self.assertEqual(results, [0])
            with self.assertRaisesRegex(SharedMemoryError, "Timed out"):
                hwinfo.read_snapshot(timeout_ms=1)
            # It remains owned by the holder after the failed read.
            self.assertEqual(self.kernel.WaitForSingleObject(self.mutex, 0), 0x102)
        finally:
            finish.set()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assert_mutex_available_to_another_thread()
        self.assertEqual(hwinfo.read_snapshot().poll_time, 123)


if __name__ == "__main__":
    unittest.main()
