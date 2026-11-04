import io
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from hwinfo_term.config import DEFAULT_CONFIG
from hwinfo_term.hwinfo import Snapshot
from hwinfo_term.model import demo_snapshot
from hwinfo_term.terminal import Canvas, Event
from hwinfo_term.tui import TUIApp


class TUIBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = deepcopy(DEFAULT_CONFIG)
        config["watches"] = [{
            "id": "cpu-watch", "sensor": "demo:cpu_temp", "label": "CPU",
            "above": 85.0, "below": 70.0, "duration": 4.0,
            "active": False, "since": None,
        }]
        self.app = TUIApp(Path(self.directory.name), config, demo=True)
        self.app._save = Mock()
        self.watch = self.app.config["watches"][0]
        self.reading = demo_snapshot(1).readings["demo:cpu_temp"]
        self.sequence = 0

    def sample(self, value, at, monotonic=None):
        self.sequence += 1
        reading = replace(self.reading, value=value)
        with patch("hwinfo_term.tui.time.time", return_value=at), patch(
            "hwinfo_term.tui.time.monotonic", return_value=at if monotonic is None else monotonic
        ):
            self.app.store.ingest(Snapshot(self.sequence, {reading.key: reading}), observed_at=at)
            records, status = self.app.store.snapshot()
            self.app._check_watches(records, status)

    def test_pending_watch_needs_continuous_above_threshold_samples(self):
        self.sample(90.0, 100.0)
        self.sample(75.0, 102.0)
        self.assertIsNone(self.watch["since"])
        self.sample(90.0, 104.0)
        self.assertFalse(self.watch["active"])
        for at in (105.0, 106.0, 107.0, 108.0):
            self.sample(90.0, at)
        self.assertTrue(self.watch["active"])
        self.assertEqual([e["state"] for e in self.app.config["events"]], ["triggered"])
        self.sample(75.0, 109.0)
        self.assertTrue(self.watch["active"], "an active watch retains hysteresis")
        self.sample(70.0, 110.0)
        self.assertFalse(self.watch["active"])
        self.assertEqual([e["state"] for e in self.app.config["events"]], ["cleared", "triggered"])

    def test_failure_without_new_sample_resets_pending_duration(self):
        self.sample(90.0, 100.0)
        with patch("hwinfo_term.tui.time.time", return_value=102.0):
            self.app.store.source_error("mapping disappeared", now=102.0)
            self.app._check_watches(*self.app.store.snapshot())
        self.assertIsNone(self.watch["since"])
        self.sample(90.0, 104.0)
        self.assertFalse(self.watch["active"])
        self.assertEqual(self.watch["since"], 104.0)

    def test_stale_without_new_sample_resets_pending_duration(self):
        self.sample(90.0, 100.0)
        with patch("hwinfo_term.tui.time.time", return_value=106.0):
            self.app._check_watches(*self.app.store.snapshot())
        self.assertIsNone(self.watch["since"])
        self.sample(90.0, 107.0)
        self.assertFalse(self.watch["active"])

    def test_outage_and_recovery_between_frames_reset_pending_duration(self):
        self.sample(90.0, 100.0)
        self.app.store.source_error("offline between frames", now=102.0)
        self.sample(90.0, 104.0)
        self.assertFalse(self.watch["active"])
        self.assertEqual(self.watch["since"], 104.0)

    def test_skipped_sample_cannot_prove_continuous_threshold(self):
        self.sample(90.0, 100.0)
        self.sequence += 1
        reading = replace(self.reading, value=75.0)
        self.app.store.ingest(Snapshot(self.sequence, {reading.key: reading}), observed_at=102.0)
        self.sample(90.0, 104.0)
        self.assertFalse(self.watch["active"])
        self.assertEqual(self.watch["since"], 104.0)

    def test_unobserved_gap_cannot_complete_duration(self):
        self.sample(90.0, 100.0)
        self.sample(90.0, 110.0)
        self.assertFalse(self.watch["active"])
        self.assertEqual(self.watch["since"], 110.0)

    def test_duration_uses_elapsed_monotonic_time(self):
        self.sample(90.0, 100.0, monotonic=10.0)
        self.sample(90.0, 104.0, monotonic=11.0)
        self.assertFalse(self.watch["active"])

    def test_equal_trigger_and_rearm_do_not_chatter(self):
        self.watch.update(below=85.0, duration=0.0)
        self.sample(85.0, 100.0)
        self.sample(85.0, 101.0)
        self.assertTrue(self.watch["active"])
        self.assertEqual(len(self.app.config["events"]), 1)

    def test_source_outage_does_not_clear_active_watch(self):
        self.watch["duration"] = 0.0
        self.sample(90.0, 100.0)
        with patch("hwinfo_term.tui.time.time", return_value=101.0):
            self.app.store.source_error("offline", now=101.0)
            self.app._check_watches(*self.app.store.snapshot())
        self.assertTrue(self.watch["active"])
        self.assertEqual(len(self.app.config["events"]), 1)

    def test_control_exit_and_eof_work_in_overlays_and_theme_editor(self):
        for overlay in (None, "palette", "filter", "cards_edit", "watch_form", "watch_delete", "theme_hex", "history_groups"):
            for event in (Event("key", key="ctrl+c"), Event("key", key="ctrl+d"), Event("eof")):
                with self.subTest(overlay=overlay, event=event):
                    self.app.quit_requested = False
                    self.app.overlay = overlay
                    self.app.theme_editing = True
                    self.app._handle_event(event)
                    self.assertTrue(self.app.quit_requested)

    def test_offline_and_stale_values_are_explicit_without_colour(self):
        self.sample(90.0, 100.0)
        with patch("hwinfo_term.tui.time.time", return_value=101.0):
            self.app.store.source_error("offline", now=101.0)
            record = self.app.store.snapshot()[0][self.reading.key]
            self.assertEqual(self.app._current_value(record), "OFFLINE")
            self.app.config["metric_bar_labels"] = True
            canvas = Canvas(60, 6, ascii_only=True)
            self.app._draw_metrics_tile(canvas, 0, 0, 60, 6, {self.reading.key: record})
            self.assertIn("OFFLINE", "".join(cell.char for row in canvas.cells for cell in row))
        self.sample(90.0, 102.0)
        with patch("hwinfo_term.tui.time.time", return_value=108.0):
            record = self.app.store.snapshot()[0][self.reading.key]
            self.assertEqual(self.app._current_value(record), "STALE")

    def test_saved_filter_is_restored(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["filter_text"] = "GPU"
        app = TUIApp(Path(self.directory.name), config)
        self.assertEqual(app.filter_text, "GPU")

    def test_sensor_keyboard_selection_is_visible_and_overscroll_is_clamped(self):
        readings = {
            f"sensor:{index:03}": replace(self.reading, key=f"sensor:{index:03}", label=f"Metric {index:03}")
            for index in range(50)
        }
        self.app.store.ingest(Snapshot(1, readings))
        self.app.view = "sensors"
        self.app.selected_sensor = "sensor:000"
        for _ in range(20):
            self.app._move_selection(1)
        canvas = Canvas(80, 20)
        records, _ = self.app.store.snapshot()
        self.app._draw_sensor_table(canvas, 0, 0, 80, 16, records)
        visible = [hit.payload for hit in self.app.hits if hit.action == "sensor_select"]
        self.assertIn("sensor:020", visible)
        self.app.tile_scroll["sensors"] = 999
        self.app.hits = []
        self.app._draw_sensor_table(canvas, 0, 0, 80, 16, records)
        visible = [hit.payload for hit in self.app.hits if hit.action == "sensor_select"]
        self.assertIn("sensor:049", visible)
        self.assertTrue(visible)

    def test_watch_scroll_and_selection_use_global_indices(self):
        self.app.config["watches"] = [dict(self.watch, id=f"w{index}", label=f"Watch {index}") for index in range(30)]
        self.app.view = "alerts"
        self.app._scroll(1, page=True)
        canvas = Canvas(80, 20)
        self.app._draw_watch_list(canvas, 0, 0, 80, 12, {})
        visible = [hit.payload for hit in self.app.hits if hit.action == "watch_select"]
        self.assertEqual(visible[0], 6)
        for _ in range(20):
            self.app._move_selection(1)
        self.app.hits = []
        self.app._draw_watch_list(canvas, 0, 0, 80, 12, {})
        visible = [hit.payload for hit in self.app.hits if hit.action == "watch_select"]
        self.assertIn(20, visible)

    def test_each_view_runs_with_hidden_and_visible_chrome(self):
        self.app.reader = Mock()
        self.app.store.ingest(demo_snapshot(1))
        for hidden in (False, True):
            for width, height in ((40, 10), (68, 18), (120, 40)):
                for view in ("dashboard", "sensors", "history", "recent", "cards", "alerts", "theme", "source", "help"):
                    with self.subTest(hidden=hidden, size=(width, height), view=view):
                        self.app.view = view
                        self.app.config["chrome_hidden"] = hidden
                        self.app.quit_requested = False
                        self.app.previous_frame = None
                        with patch("hwinfo_term.tui.TerminalSession") as terminal, patch(
                            "hwinfo_term.tui.shutil.get_terminal_size", return_value=(width, height)
                        ), patch("hwinfo_term.tui.sys.stdout", new_callable=io.StringIO) as stdout:
                            terminal.return_value.__enter__.return_value.next_event.return_value = Event("eof")
                            self.assertEqual(self.app.run(), 0)
                            self.assertTrue(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
