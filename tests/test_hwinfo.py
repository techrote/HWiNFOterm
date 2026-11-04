import struct
import unittest

from hwinfo_term.hwinfo import (
    HEADER_ALIGNED,
    HEADER_PACKED,
    READING_ALIGNED,
    READING_PACKED,
    SharedMemoryError,
    parse_mapping,
)


SIGNATURE = int.from_bytes(b"HWiS", "little")


def _put_text(buffer: bytearray, offset: int, size: int, value: bytes) -> None:
    buffer[offset : offset + size] = value[:size].ljust(size, b"\0")


def make_mapping(header_format, reading_format, extended=False):
    sensor_size = 392 if extended else 264
    reading_size = reading_format.size + (144 if extended else 0)
    sensor_offset = header_format.size
    reading_offset = sensor_offset + sensor_size
    buffer = bytearray(reading_offset + reading_size)
    header_format.pack_into(
        buffer,
        0,
        SIGNATURE,
        2,
        1,
        1_800_000_000,
        sensor_offset,
        sensor_size,
        1,
        reading_offset,
        reading_size,
        1,
    )

    _sensor_id = 0xF0002A00
    struct.pack_into("<II", buffer, sensor_offset, _sensor_id, 0)
    _put_text(buffer, sensor_offset + 8, 128, b"CPU original")
    _put_text(buffer, sensor_offset + 136, 128, b"CPU Package")
    if extended:
        _put_text(buffer, sensor_offset + 264, 128, "CPU — Package".encode("utf-8"))

    reading_offset_in_row = reading_offset
    reading_format.pack_into(
        buffer,
        reading_offset_in_row,
        1,
        0,
        0x1000004,
        b"Core Temperature",
        b"Package Temperature",
        b"C",
        73.25,
        31.0,
        96.5,
        58.75,
    )
    if extended:
        _put_text(buffer, reading_offset_in_row + reading_format.size, 128, "Package 温度".encode("utf-8"))
        _put_text(buffer, reading_offset_in_row + reading_format.size + 128, 16, "°C".encode("utf-8"))
    return bytes(buffer)


class SharedMemoryParserTests(unittest.TestCase):
    def test_packed_v2_rows(self):
        result = parse_mapping(make_mapping(HEADER_PACKED, READING_PACKED))
        reading = next(iter(result.readings.values()))
        self.assertEqual(reading.sensor, "CPU Package")
        self.assertEqual(reading.label, "Package Temperature")
        self.assertEqual(reading.value, 73.25)
        self.assertEqual(reading.minimum, 31.0)
        self.assertEqual(reading.maximum, 96.5)
        self.assertEqual(reading.average, 58.75)
        self.assertEqual(reading.unit, "C")

    def test_aligned_v2_rows(self):
        result = parse_mapping(make_mapping(HEADER_ALIGNED, READING_ALIGNED))
        reading = next(iter(result.readings.values()))
        self.assertEqual(reading.value, 73.25)
        self.assertEqual(reading.average, 58.75)

    def test_extended_utf8_rows(self):
        packed = parse_mapping(make_mapping(HEADER_PACKED, READING_PACKED, extended=True))
        aligned = parse_mapping(make_mapping(HEADER_ALIGNED, READING_ALIGNED, extended=True))
        packed_reading = next(iter(packed.readings.values()))
        aligned_reading = next(iter(aligned.readings.values()))
        self.assertEqual(packed_reading.sensor, "CPU — Package")
        self.assertEqual(packed_reading.label, "Package 温度")
        self.assertEqual(packed_reading.unit, "°C")
        self.assertEqual(aligned_reading.label, "Package 温度")
        self.assertEqual(aligned_reading.unit, "°C")
        self.assertEqual(aligned_reading.value, 73.25)

    def test_inactive_mapping_is_rejected(self):
        mapping = bytearray(make_mapping(HEADER_PACKED, READING_PACKED))
        mapping[:4] = b"DEAD"
        with self.assertRaisesRegex(SharedMemoryError, "inactive"):
            parse_mapping(bytes(mapping))

    def test_unsupported_version_is_rejected(self):
        mapping = bytearray(make_mapping(HEADER_PACKED, READING_PACKED))
        struct.pack_into("<I", mapping, 4, 99)
        with self.assertRaisesRegex(SharedMemoryError, "Unsupported.*99"):
            parse_mapping(bytes(mapping))


if __name__ == "__main__":
    unittest.main()
