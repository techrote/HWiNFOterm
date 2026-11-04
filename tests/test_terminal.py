import io
import os
import unittest
from unittest import mock

from hwinfo_term.terminal import Canvas, Event, TerminalSession, _safe_text


class PlainTheme:
    def ansi(self, role, foreground):
        return ""


class CanvasTests(unittest.TestCase):
    def test_overwrite_wide_leading_cell_clears_continuation(self):
        canvas = Canvas(4, 1)
        canvas.put(0, 0, "界A")
        canvas.put(0, 0, "x")
        self.assertEqual([cell.char for cell in canvas.cells[0]], ["x", " ", "A", " "])
        self.assertFalse(any(cell.continuation for cell in canvas.cells[0]))

    def test_overwrite_wide_continuation_clears_leading_cell(self):
        canvas = Canvas(4, 1)
        canvas.put(0, 0, "界A")
        canvas.put(1, 0, "x")
        self.assertEqual([cell.char for cell in canvas.cells[0]], [" ", "x", "A", " "])

    def test_fill_cannot_leave_half_a_wide_character(self):
        canvas = Canvas(4, 1)
        canvas.put(0, 0, "界A")
        canvas.fill(1, 0, 1, ".")
        self.assertEqual([cell.char for cell in canvas.cells[0]], [" ", ".", "A", " "])

    def test_wide_to_narrow_diff_repaints_both_columns(self):
        before = Canvas(4, 1)
        before.put(0, 0, "界A")
        _, previous = before.render(PlainTheme())
        after = Canvas(4, 1)
        after.put(0, 0, "x A")
        output, _ = after.render(PlainTheme(), previous)
        self.assertIn("x ", output)
        self.assertTrue(output.startswith("\x1b[1;1H"))

    def test_combining_mark_attaches_to_wide_character(self):
        canvas = Canvas(4, 1)
        self.assertEqual(canvas.put(0, 0, "界\u0301A"), 3)
        self.assertEqual(canvas.cells[0][0].char, "界\u0301")
        self.assertTrue(canvas.cells[0][1].continuation)
        self.assertEqual(canvas.cells[0][2].char, "A")

    def test_negative_x_clips_text_without_shifting_it(self):
        canvas = Canvas(4, 1)
        canvas.put(-2, 0, "abcd")
        self.assertEqual([cell.char for cell in canvas.cells[0]], ["c", "d", " ", " "])

    def test_ascii_mode_applies_to_fills_and_lines(self):
        canvas = Canvas(6, 2, ascii_only=True)
        canvas.fill(0, 0, 6, "█")
        canvas.hline(0, 1, 6)
        output, _ = canvas.render(PlainTheme())
        self.assertTrue(output.isascii())
        self.assertIn("######", output)
        self.assertIn("------", output)

    def test_display_text_preserves_word_boundaries_and_strips_controls(self):
        self.assertEqual(_safe_text("CPU\nPackage\tTemp\rC\x1b\x07"), "CPU Package Temp C")


class TerminalInputTests(unittest.TestCase):
    def setUp(self):
        self.session = TerminalSession()

    def feed(self, text):
        for char in text:
            self.session._input_queue.put(char)

    def test_focus_out_does_not_swallow_next_key(self):
        self.feed("\x1b[Oq")
        self.assertEqual(self.session.next_event(0), Event("focus", action="out"))
        self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_unknown_csi_does_not_swallow_next_key(self):
        self.feed("\x1b[?9zq")
        self.assertIsNone(self.session.next_event(0))
        self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_escape_before_ordinary_text_preserves_both(self):
        self.feed("\x1bq")
        self.assertEqual(self.session.next_event(0), Event("key", key="escape"))
        self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_adjacent_escapes_preserve_both(self):
        self.feed("\x1b\x1b")
        self.assertEqual(self.session.next_event(0), Event("key", key="escape"))
        self.assertEqual(self.session.next_event(0), Event("key", key="escape"))

    def test_fragmented_mouse_packet_is_retained(self):
        self.feed("\x1b[<0;13;")
        self.assertIsNone(self.session.next_event(0))
        self.feed("5Mq")
        self.assertEqual(self.session.next_event(0), Event("mouse", x=12, y=4, button=0, action="press"))
        self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_overlong_report_is_bounded_and_does_not_consume_next_key(self):
        self.feed("\x1b[" + "9" * 300 + "zq")
        self.assertIsNone(self.session.next_event(0))
        self.assertLessEqual(len(self.session._ansi_buffer), 128)
        self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_truncated_report_expires_before_next_key(self):
        self.feed("\x1b[<0;")
        self.assertIsNone(self.session.next_event(0))
        self.feed("q")
        with mock.patch("time.monotonic", return_value=self.session._ansi_deadline + 1):
            self.assertEqual(self.session.next_event(0), Event("text", key="q", text="q"))

    def test_mouse_release_preserves_modifiers(self):
        self.feed("\x1b[<20;13;5m")
        self.assertEqual(self.session.next_event(0), Event("mouse", x=12, y=4, button=0, action="release", control=True, shift=True))

    def test_combined_keyboard_modifiers(self):
        self.feed("\x1b[1;6D\x1b[3;6~")
        self.assertEqual(self.session.next_event(0), Event("key", key="left", control=True, shift=True))
        self.assertEqual(self.session.next_event(0), Event("key", key="delete", control=True, shift=True))

    def test_application_cursor_keys_and_f2(self):
        self.feed("\x1bOA\x1bOQ")
        self.assertEqual(self.session.next_event(0), Event("key", key="up"))
        self.assertEqual(self.session.next_event(0), Event("key", key="f2"))

    def test_eof_is_an_explicit_event(self):
        self.session._input_queue.put(None)
        self.assertEqual(self.session.next_event(0), Event("eof"))

    @unittest.skipIf(os.name == "nt", "POSIX input path")
    def test_posix_utf8_is_decoded_across_reads_and_eof_ends_reader(self):
        raw = "à温度".encode("utf-8")
        chunks = [bytes([value]) for value in raw] + [b""]
        with mock.patch("sys.stdin") as stdin, mock.patch("select.select", return_value=([7], [], [])), mock.patch("os.read", side_effect=chunks) as read:
            stdin.fileno.return_value = 7
            self.session._read_loop()
        self.assertEqual(read.call_count, len(chunks))
        for char in "à温度":
            self.assertEqual(self.session.next_event(0), Event("text", key=char.lower(), text=char))
        self.assertEqual(self.session.next_event(0), Event("eof"))


class TerminalLifecycleTests(unittest.TestCase):
    def test_windows_modes_are_raw_and_restore_original_values(self):
        session = TerminalSession()
        kernel = mock.Mock()
        original_modes = {10: 0x00F7, 11: 0x0003}
        kernel.GetStdHandle.side_effect = lambda number: 10 if number == 0xFFFFFFF6 else 11

        def get_mode(handle, value):
            value._obj.value = original_modes[handle]
            return 1

        kernel.GetConsoleMode.side_effect = get_mode
        kernel.SetConsoleMode.return_value = 1
        msvcrt = mock.Mock()
        msvcrt.setmode.return_value = 123
        with mock.patch("ctypes.WinDLL", return_value=kernel, create=True), mock.patch.dict("sys.modules", {"msvcrt": msvcrt}), mock.patch.object(os, "O_BINARY", 0x8000, create=True), mock.patch("sys.stdin") as stdin:
            stdin.fileno.return_value = 0
            session._enter_windows()
            requested_input = kernel.SetConsoleMode.call_args_list[0].args[1]
            requested_output = kernel.SetConsoleMode.call_args_list[1].args[1]
            self.assertTrue(requested_input & 0x0200)  # VT input
            self.assertTrue(requested_input & 0x0080)  # extended flags
            self.assertEqual(requested_input & 0x0047, 0)  # no echo/line/processed/Quick Edit
            self.assertEqual(requested_output & 0x000D, 0x000D)  # VT, processed, delayed wrap
            with mock.patch.object(os, "name", "nt"), mock.patch("sys.stdout", io.StringIO()):
                session.close()
            self.assertEqual(kernel.SetConsoleMode.call_args_list[-2:], [mock.call(11, 0x0003), mock.call(10, 0x00F7)])
            self.assertEqual(msvcrt.setmode.call_args_list, [mock.call(0, 0x8000), mock.call(0, 123)])

    def test_reader_is_stopped_before_console_modes_are_restored(self):
        session = TerminalSession()
        steps = []
        session._reader = mock.Mock()
        session._reader.is_alive.return_value = True
        session._reader.join.side_effect = lambda **kwargs: steps.append("joined")
        session._kernel32 = mock.Mock()
        session._kernel32.SetConsoleMode.side_effect = lambda *args: steps.append("restored")
        session._console_modes = [(10, 123)]
        with mock.patch.object(os, "name", "nt"), mock.patch("sys.stdout", io.StringIO()):
            session.close()
        self.assertEqual(steps, ["joined", "restored"])

    def test_failed_enter_restores_terminal(self):
        session = TerminalSession()
        with mock.patch.object(session, "_enter_posix"), mock.patch.object(session, "_enter_windows"), mock.patch.object(session, "close") as close, mock.patch("sys.stdout") as stdout:
            stdout.write.side_effect = OSError("output closed")
            with self.assertRaises(OSError):
                session.__enter__()
            close.assert_called_once_with()

    def test_closed_stdout_does_not_prevent_mode_restoration(self):
        session = TerminalSession()
        session._termios_state = (7, [1, 2, 3])
        output = io.StringIO()
        output.close()
        fake_termios = mock.Mock(TCSADRAIN=1)
        with mock.patch("sys.stdout", output), mock.patch.dict("sys.modules", {"termios": fake_termios}):
            session.close()
        fake_termios.tcsetattr.assert_called_once_with(7, 1, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
