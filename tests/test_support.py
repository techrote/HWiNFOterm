import unittest

from hwinfo_term.config import normalize
from hwinfo_term.model import TelemetryStore
from hwinfo_term.theme import make_theme, sample_palette, theme_document
from hwinfo_term.hwinfo import Reading, Snapshot


class SupportBehaviorTests(unittest.TestCase):
    def test_config_rejects_nonfinite_watch_thresholds(self):
        config, _ = normalize({
            "version": 1,
            "watches": [{"id": "bad", "sensor": "s", "above": float("inf"), "below": 0}],
        })
        self.assertEqual(config["watches"], [])

    def test_theme_lut_is_256_entries_and_seeded(self):
        theme = make_theme("LUTEX Night")
        palette = theme_document(theme)["palette"]
        self.assertEqual(len(sample_palette(palette)), 256)
        palette["variation"] = 35
        first = sample_palette(palette)
        self.assertEqual(first, sample_palette(palette))
        palette["seed"] += 1
        self.assertNotEqual(first, sample_palette(palette))

    def test_snapshot_isolates_history_buffer(self):
        store = TelemetryStore(history_seconds=60)
        reading = Reading(
            key="f0002a00:0:01000004",
            sensor_id=0xF0002A00,
            sensor_instance=0,
            reading_id=0x01000004,
            sensor="CPU",
            label="Package",
            unit="°C",
            kind="temperature",
            value=70.0,
            minimum=None,
            maximum=None,
            average=None,
        )
        store.ingest(Snapshot(poll_time=1, readings={reading.key: reading}), observed_at=100.0)
        records, _ = store.snapshot()
        records[reading.key].history.append((101.0, 80.0))
        self.assertEqual(store.history_for(reading.key), [(100.0, 70.0)])


if __name__ == "__main__":
    unittest.main()
