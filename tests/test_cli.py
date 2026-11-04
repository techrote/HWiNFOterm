import io
import json
import unittest
from copy import deepcopy
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from hwinfo_term.cli import main
from hwinfo_term.config import DEFAULT_CONFIG
from hwinfo_term.hwinfo import Reading, SharedMemoryError, Snapshot


def snapshot_with_labels(sensor="CPU", label="Temperature", unit="°C"):
    reading = Reading("sensor:reading", 1, 0, 2, sensor, label, unit, "temperature", 70.0, 60.0, 80.0, 65.0)
    return Snapshot(1, {reading.key: reading})


class CLITests(unittest.TestCase):
    def test_default_demo_state_is_isolated_and_explicit_state_dir_is_honored(self):
        for arguments, expected in (([], Path("state")), (["--demo"], Path("state") / "demo"), (["--demo", "--state-dir", "chosen"], Path("chosen"))):
            with self.subTest(arguments=arguments), patch("hwinfo_term.cli.default_state_dir", return_value=Path("state")), patch(
                "hwinfo_term.cli.load_config", return_value=(deepcopy(DEFAULT_CONFIG), None)
            ) as load, patch("hwinfo_term.cli.TUIApp") as app, patch(
                "hwinfo_term.cli.sys.stdin.isatty", return_value=True
            ), patch("hwinfo_term.cli.sys.stdout.isatty", return_value=True):
                app.return_value.run.return_value = 0
                self.assertEqual(main(arguments), 0)
                load.assert_called_once_with(expected)
                self.assertEqual(app.call_args.kwargs["state_dir"], expected)

    def test_demo_json_is_explicit_and_does_not_read_live_source(self):
        output = io.StringIO()
        with patch("hwinfo_term.cli.read_snapshot") as read, redirect_stdout(output):
            result = main(["--demo", "--once", "--json"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["provider"], "simulated-demo")
        read.assert_not_called()

    def test_live_source_failure_does_not_substitute_demo_data(self):
        output, error = io.StringIO(), io.StringIO()
        with patch("hwinfo_term.cli.read_snapshot", side_effect=SharedMemoryError("mapping unavailable")), redirect_stdout(output), redirect_stderr(error):
            result = main(["--once", "--json"])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("mapping unavailable", error.getvalue())

    def test_json_requires_a_one_shot_mode(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--json"])
        self.assertEqual(caught.exception.code, 2)

    def test_nonfinite_intervals_are_rejected_even_in_one_shot_mode(self):
        for option in ("--poll-interval", "--stale-after"):
            for value in ("nan", "inf", "-inf"):
                with self.subTest(option=option, value=value):
                    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                        main([f"{option}={value}", "--demo", "--once"])
                    self.assertEqual(caught.exception.code, 2)

    def test_plain_output_does_not_emit_provider_control_sequences(self):
        snap = snapshot_with_labels("CPU\x1b[2J", "Temperature\nforged-row", "°C\x07")
        for mode in ("--scan", "--once"):
            with self.subTest(mode=mode):
                output = io.StringIO()
                with patch("hwinfo_term.cli.read_snapshot", return_value=snap), redirect_stdout(output):
                    result = main([mode])
                self.assertEqual(result, 0)
                self.assertNotIn("\x1b", output.getvalue())
                self.assertNotIn("\x07", output.getvalue())
                self.assertNotIn("\nforged-row", output.getvalue())

    def test_json_is_lossless_on_non_utf8_redirected_stdout(self):
        snap = snapshot_with_labels("CPU 温度", "Temperature\x1b[2J", "°C")
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding="cp1252")
        self.addCleanup(output.close)
        with patch("hwinfo_term.cli.read_snapshot", return_value=snap), redirect_stdout(output):
            result = main(["--once", "--json"])
        output.flush()
        self.assertEqual(result, 0)
        document = json.loads(buffer.getvalue().decode("ascii"))
        self.assertEqual(document["readings"][0]["sensor"], "CPU 温度")
        self.assertEqual(document["readings"][0]["label"], "Temperature\x1b[2J")


if __name__ == "__main__":
    unittest.main()
