import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from hwinfo_term.config import DEFAULT_CONFIG, TILES, load_config, normalize, save_config


class ConfigNormalizationTests(unittest.TestCase):
    def test_invalid_colour_mode_uses_default_without_crashing(self):
        for value in ([], {}, None, 7):
            with self.subTest(value=value):
                config, error = normalize({"version": 1, "colour_mode": value})
                self.assertEqual(config["colour_mode"], DEFAULT_CONFIG["colour_mode"])
                self.assertIsNotNone(error)

    def test_duplicate_hidden_tiles_never_crash_or_hide_every_tile(self):
        for hidden in (
            ["sensors", "history", "alerts", "alerts"],
            TILES + ["alerts"],
        ):
            with self.subTest(hidden=hidden):
                config, _ = normalize({"version": 1, "hidden_tiles": hidden})
                self.assertEqual(len(config["hidden_tiles"]), len(set(config["hidden_tiles"])))
                self.assertTrue(set(TILES) - set(config["hidden_tiles"]))

    def test_nonfinite_split_ratios_use_defaults(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                config, _ = normalize({"version": 1, "column_ratio": value, "row_ratio": value})
                self.assertEqual(config["column_ratio"], DEFAULT_CONFIG["column_ratio"])
                self.assertEqual(config["row_ratio"], DEFAULT_CONFIG["row_ratio"])

    def test_cards_preserve_duplicate_sensor_assignments_and_order(self):
        config, _ = normalize({"version": 1, "cards_keys": ["cpu", "gpu", "cpu"]})
        self.assertEqual(config["cards_keys"], ["cpu", "gpu", "cpu"])

    def test_cards_are_bounded_to_the_ui_card_limit(self):
        config, _ = normalize({"version": 1, "cards_keys": ["cpu"] * 300})
        self.assertEqual(config["cards_keys"], ["cpu"] * 256)

    def test_nonfinite_watch_duration_is_not_changed_into_an_immediate_alarm(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                config, _ = normalize({
                    "version": 1,
                    "watches": [{"id": "w", "sensor": "cpu", "above": 85, "below": 70, "duration": value}],
                })
                self.assertEqual(config["watches"], [])

    def test_pending_duration_is_reset_but_active_watch_state_survives_reload(self):
        config, _ = normalize({
            "version": 1,
            "watches": [
                {"id": "pending", "sensor": "cpu", "above": 85, "below": 70, "since": 100},
                {"id": "active", "sensor": "cpu", "above": 85, "below": 70, "active": True, "since": 100},
            ],
        })
        self.assertEqual([watch["since"] for watch in config["watches"]], [None, None])
        self.assertEqual([watch["active"] for watch in config["watches"]], [False, True])

    def test_string_false_does_not_suppress_watch_as_already_active(self):
        config, _ = normalize({
            "version": 1,
            "watches": [{"id": "w", "sensor": "cpu", "above": 85, "below": 70, "active": "false"}],
        })
        self.assertFalse(config["watches"][0]["active"])

    def test_nonfinite_custom_theme_cannot_poison_subsequent_saves(self):
        config, error = normalize({
            "version": 1,
            "theme_document": {"theme": "LUTEX Night", "palette": {"variation": float("nan")}},
        })
        self.assertEqual(config["theme_document"], {})
        self.assertIsNotNone(error)
        json.dumps(config, allow_nan=False)


class ConfigStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)
        self.path = self.state_dir / "config.json"
        self.backup = self.state_dir / "config.json.bak"

    def test_corrupt_primary_recovers_backup_without_changing_files(self):
        self.path.write_text("{broken", encoding="utf-8")
        backup_text = '{"version": 1, "pinned": ["cpu"], "cards_keys": ["cpu", "cpu"]}'
        self.backup.write_text(backup_text, encoding="utf-8")
        config, error = load_config(self.state_dir)
        self.assertEqual(config["pinned"], ["cpu"])
        self.assertEqual(config["cards_keys"], ["cpu", "cpu"])
        self.assertIn("backup", error.lower())
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(self.backup.read_text(encoding="utf-8"), backup_text)

    def test_missing_or_wrong_root_primary_recovers_backup(self):
        self.backup.write_text('{"version": 1, "pinned": ["cpu"]}', encoding="utf-8")
        for primary in (None, "[]", "null"):
            with self.subTest(primary=primary):
                if primary is not None:
                    self.path.write_text(primary, encoding="utf-8")
                config, error = load_config(self.state_dir)
                self.assertEqual(config["pinned"], ["cpu"])
                self.assertIn("backup", error.lower())

    def test_unusable_primary_and_backup_return_defaults_with_warning(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.backup.write_text("[broken", encoding="utf-8")
        config, error = load_config(self.state_dir)
        self.assertEqual(config, DEFAULT_CONFIG)
        self.assertIsNotNone(error)

    def test_first_run_without_config_or_backup_is_not_an_error(self):
        config, error = load_config(self.state_dir)
        self.assertEqual(config, DEFAULT_CONFIG)
        self.assertIsNone(error)

    def test_saving_recovered_config_preserves_good_backup(self):
        self.path.write_text("{broken", encoding="utf-8")
        backup_text = '{"version": 1, "pinned": ["cpu"]}'
        self.backup.write_text(backup_text, encoding="utf-8")
        config, _ = load_config(self.state_dir)
        save_config(self.state_dir, config)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["pinned"], ["cpu"])
        self.assertEqual(self.backup.read_text(encoding="utf-8"), backup_text)

    def test_successful_save_keeps_previous_valid_config_as_backup(self):
        config = deepcopy(DEFAULT_CONFIG)
        save_config(self.state_dir, config)
        previous = self.path.read_bytes()
        config["pinned"] = ["cpu"]
        save_config(self.state_dir, config)
        self.assertEqual(self.backup.read_bytes(), previous)
        self.assertEqual(load_config(self.state_dir)[0]["pinned"], ["cpu"])

    def test_failed_primary_replace_preserves_primary_and_cleans_temporary_files(self):
        config = deepcopy(DEFAULT_CONFIG)
        save_config(self.state_dir, config)
        previous = self.path.read_bytes()
        config["pinned"] = ["cpu"]
        from hwinfo_term.config import os
        real_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.path:
                raise OSError("simulated replacement failure")
            real_replace(source, destination)

        with patch("hwinfo_term.config.os.replace", side_effect=replace):
            with self.assertRaises(OSError):
                save_config(self.state_dir, config)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.state_dir.glob("*.tmp")), [])

    def test_failed_backup_replace_preserves_old_backup_and_still_saves_primary(self):
        config = deepcopy(DEFAULT_CONFIG)
        save_config(self.state_dir, config)
        backup_text = '{"version": 1, "pinned": ["old"]}'
        self.backup.write_text(backup_text, encoding="utf-8")
        config["pinned"] = ["new"]
        from hwinfo_term.config import os
        real_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.backup:
                raise OSError("simulated backup replacement failure")
            real_replace(source, destination)

        with patch("hwinfo_term.config.os.replace", side_effect=replace):
            save_config(self.state_dir, config)
        self.assertEqual(self.backup.read_text(encoding="utf-8"), backup_text)
        self.assertEqual(load_config(self.state_dir)[0]["pinned"], ["new"])
        self.assertEqual(list(self.state_dir.glob("*.tmp")), [])

    def test_unserializable_config_does_not_replace_existing_files(self):
        config = deepcopy(DEFAULT_CONFIG)
        save_config(self.state_dir, config)
        save_config(self.state_dir, config)
        primary, backup = self.path.read_bytes(), self.backup.read_bytes()
        config["poll_interval"] = float("nan")
        with self.assertRaises(ValueError):
            save_config(self.state_dir, config)
        self.assertEqual(self.path.read_bytes(), primary)
        self.assertEqual(self.backup.read_bytes(), backup)
        self.assertEqual(list(self.state_dir.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
