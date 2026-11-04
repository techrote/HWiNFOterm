"""Windows Terminal VT session, normalized input events, and cell compositor."""

from __future__ import annotations

import codecs
import os
import queue
import re
import sys
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import Any


ESC = "\x1b"
CSI = ESC + "["


def cell_width(char: str) -> int:
    if not char:
        return 0
    if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


@dataclass(frozen=True, slots=True)
class Cell:
    char: str = " "
    foreground: str = "text.primary"
    background: str = "surface.base"
    bold: bool = False
    continuation: bool = False


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    key: str = ""
    text: str = ""
    x: int = -1
    y: int = -1
    button: int = -1
    action: str = ""
    wheel: int = 0
    control: bool = False
    shift: bool = False


class Canvas:
    def __init__(self, width: int, height: int, ascii_only: bool = False):
        self.width = max(1, width)
        self.height = max(1, height)
        self.ascii_only = ascii_only
        self.cells = [[Cell() for _ in range(self.width)] for _ in range(self.height)]

    def put(
        self,
        x: int,
        y: int,
        text: Any,
        foreground: str = "text.primary",
        background: str = "surface.base",
        bold: bool = False,
        max_width: int | None = None,
    ) -> int:
        if y < 0 or y >= self.height or x >= self.width:
            return x
        limit = self.width if max_width is None else min(self.width, x + max(0, max_width))
        cursor = x
        raw = _safe_text(str(text))
        for char in raw:
            if self.ascii_only and ord(char) > 127:
                char = _ASCII.get(char, "?")
            width = cell_width(char)
            if width == 0:
                if cursor > 0 and cursor - 1 < self.width:
                    column = cursor - 1
                    if self.cells[y][column].continuation:
                        column -= 1
                    if column >= 0:
                        previous = self.cells[y][column]
                        self.cells[y][column] = Cell(previous.char + char, foreground, background, bold)
                        if column + 1 < self.width and self.cells[y][column + 1].continuation:
                            self.cells[y][column + 1] = Cell("", foreground, background, bold, True)
                continue
            if cursor < 0:
                cursor += width
                continue
            if cursor + width > limit:
                break
            self._clear_wide_glyph(cursor, y)
            if width == 2:
                self._clear_wide_glyph(cursor + 1, y)
            self.cells[y][cursor] = Cell(char, foreground, background, bold)
            if width == 2:
                self.cells[y][cursor + 1] = Cell("", foreground, background, bold, True)
            cursor += width
        return cursor

    def _clear_wide_glyph(self, x: int, y: int) -> None:
        """Erase both cells when a draw overlaps either half of a wide glyph."""
        row = self.cells[y]
        leading = x - 1 if row[x].continuation else x
        if leading >= 0 and leading + 1 < self.width and row[leading + 1].continuation:
            for column in (leading, leading + 1):
                cell = row[column]
                row[column] = Cell(" ", cell.foreground, cell.background, cell.bold)

    def fill(self, x: int, y: int, width: int, char: str = " ", foreground: str = "text.primary", background: str = "surface.base") -> None:
        if y < 0 or y >= self.height or width <= 0:
            return
        glyph = _safe_text(char)[:1] or " "
        if self.ascii_only and ord(glyph) > 127:
            glyph = _ASCII.get(glyph, "?")
        # A fill repeats one column at a time; wide/combining glyphs cannot be
        # represented by independent cells without corrupting the compositor.
        if cell_width(glyph) != 1:
            glyph = "?"
        for column in range(max(0, x), min(self.width, x + width)):
            self._clear_wide_glyph(column, y)
            self.cells[y][column] = Cell(glyph, foreground, background)

    def hline(self, x: int, y: int, width: int, char: str = "─", foreground: str = "border.normal", background: str = "surface.base") -> None:
        self.fill(x, y, width, char, foreground, background)

    def rect(self, x: int, y: int, width: int, height: int, foreground: str = "border.normal", background: str = "surface.tile") -> None:
        if width < 2 or height < 2:
            return
        horizontal, vertical = ("-", "|") if self.ascii_only else ("─", "│")
        top_left, top_right, bottom_left, bottom_right = ("+", "+", "+", "+") if self.ascii_only else ("┌", "┐", "└", "┘")
        self.fill(x, y, width, " ", "text.primary", background)
        for row in range(y + 1, min(self.height, y + height - 1)):
            self.fill(x, row, width, " ", "text.primary", background)
            self.put(x, row, vertical, foreground, background)
            self.put(x + width - 1, row, vertical, foreground, background)
        self.put(x, y, top_left, foreground, background)
        self.put(x + width - 1, y, top_right, foreground, background)
        self.put(x, y + height - 1, bottom_left, foreground, background)
        self.put(x + width - 1, y + height - 1, bottom_right, foreground, background)
        self.fill(x + 1, y, width - 2, horizontal, foreground, background)
        self.fill(x + 1, y + height - 1, width - 2, horizontal, foreground, background)

    def render(self, theme: Any, previous: list[list[Cell]] | None = None) -> tuple[str, list[list[Cell]]]:
        out: list[str] = []
        for row, cells in enumerate(self.cells):
            old = previous[row] if previous and row < len(previous) and len(previous[row]) == self.width else None
            if old is not None and cells == old:
                continue
            start = 0
            if old is not None:
                changed = [i for i, cell in enumerate(cells) if cell != old[i]]
                if not changed:
                    continue
                start = changed[0]
                end = changed[-1] + 1
                if start > 0 and (cells[start].continuation or old[start].continuation):
                    start -= 1
                if end < self.width and (cells[end].continuation or old[end].continuation):
                    end += 1
            else:
                end = self.width
            out.append(f"{CSI}{row + 1};{start + 1}H")
            active: tuple[str, str, bool] | None = None
            for cell in cells[start:end]:
                style = (cell.foreground, cell.background, cell.bold)
                if style != active:
                    out.append("\x1b[0m")
                    if cell.bold:
                        out.append("\x1b[1m")
                    out.append(theme.ansi(cell.foreground, True))
                    out.append(theme.ansi(cell.background, False))
                    active = style
                if cell.continuation:
                    continue
                out.append(cell.char)
            out.append("\x1b[0m")
        if out:
            out.append(f"{CSI}{self.height};1H")
            return "".join(out), [row.copy() for row in self.cells]
        return "", [row.copy() for row in self.cells]


_ASCII = {
    "─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "█": "#",
    "▉": "#", "▊": "#", "▋": "#", "▌": "#", "▍": "#", "▎": "#",
    "▏": "#", "░": ".", "▒": ":", "▓": "#", "●": "*", "○": "o",
    "◆": "*", "▶": ">", "◀": "<", "▲": "^", "▼": "v", "°": "o",
    "→": ">", "←": "<", "…": ".", "×": "x", "✓": "v", "⚠": "!",
}


def _safe_text(value: str) -> str:
    # Sensor names and labels are untrusted display data: strip controls/terminal escapes.
    cleaned = []
    for char in value:
        if char in "\r\n\t":
            cleaned.append(" ")
        elif unicodedata.category(char) not in {"Cc", "Cs"}:
            cleaned.append(char)
    return "".join(cleaned)


class TerminalSession:
    def __init__(self, mouse: bool = True):
        self.mouse = mouse
        self._console_modes: list[tuple[Any, int]] = []
        self._input_file_mode: int | None = None
        self._kernel32: Any = None
        self._termios_state = None
        self._input_queue: queue.Queue[str | None] = queue.Queue()
        self._pending_input: deque[str | None] = deque()
        self._reader: threading.Thread | None = None
        self._closed = False
        self._ansi_buffer = ""
        self._ansi_deadline = 0.0
        self._ansi_overflow = False

    def __enter__(self) -> "TerminalSession":
        try:
            if os.name == "nt":
                self._enter_windows()
            else:
                self._enter_posix()
            sys.stdout.write(CSI + "?1049h" + CSI + "?25l" + CSI + "2J" + CSI + "H")
            if self.mouse:
                sys.stdout.write(CSI + "?1000h" + CSI + "?1002h" + CSI + "?1006h" + CSI + "?1004h")
            sys.stdout.flush()
            self._reader = threading.Thread(target=self._read_loop, daemon=True, name="terminal-input")
            self._reader.start()
            return self
        except BaseException:
            # __exit__ is not called when entering the context fails.
            self.close()
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _enter_windows(self) -> None:
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes as w

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [w.DWORD]
            kernel.GetStdHandle.restype = w.HANDLE
            kernel.GetConsoleMode.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
            kernel.GetConsoleMode.restype = w.BOOL
            kernel.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
            kernel.SetConsoleMode.restype = w.BOOL
            self._kernel32 = kernel
            for standard_handle in (0xFFFFFFF6, 0xFFFFFFF5):
                handle = kernel.GetStdHandle(standard_handle)
                mode = w.DWORD()
                if handle and kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    if standard_handle == 0xFFFFFFF6:
                        # VT input, raw keys, no Quick Edit selection pauses.
                        requested = (mode.value | 0x0200 | 0x0080) & ~(0x0001 | 0x0002 | 0x0004 | 0x0040)
                        fallback = requested & ~0x0200
                    else:
                        # Delay wrapping at the final screen cell so a complete
                        # frame cannot scroll before the next cursor command.
                        requested = mode.value | 0x0001 | 0x0004 | 0x0008
                        fallback = mode.value | 0x0001 | 0x0004
                    if kernel.SetConsoleMode(handle, requested) or kernel.SetConsoleMode(handle, fallback):
                        self._console_modes.append((handle, mode.value))
            self._input_file_mode = msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        except (AttributeError, OSError, ValueError):
            pass

    def _enter_posix(self) -> None:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._termios_state = (fd, termios.tcgetattr(fd))
            tty.setraw(fd)
        except (ImportError, OSError, ValueError):
            self._termios_state = None

    def _read_loop(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                while not self._closed:
                    if msvcrt.kbhit():
                        self._input_queue.put(msvcrt.getwch())
                    else:
                        time.sleep(0.01)
            else:
                import select

                fd = sys.stdin.fileno()
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while not self._closed:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if ready and not self._closed:
                        value = os.read(fd, 1)
                        if not value:
                            for char in decoder.decode(b"", final=True):
                                self._input_queue.put(char)
                            self._input_queue.put(None)
                            return
                        for char in decoder.decode(value):
                            self._input_queue.put(char)
        except (EOFError, OSError, ValueError):
            self._input_queue.put(None)

    def _next_char(self, timeout: float) -> str | None:
        if self._pending_input:
            return self._pending_input.popleft()
        return self._input_queue.get(timeout=max(0.0, timeout))

    def next_event(self, timeout: float = 0.08) -> Event | None:
        if self._ansi_buffer:
            if time.monotonic() < self._ansi_deadline:
                return self._finish_vt_event()
            self._ansi_buffer = ""
        try:
            char = self._next_char(timeout)
        except queue.Empty:
            return None
        if char is None:
            return Event("eof")
        if os.name == "nt" and char in {"\x00", "\xe0"}:
            try:
                code = self._next_char(0.05)
            except queue.Empty:
                return self._plain_event(char)
            if code is None:
                return Event("eof")
            key = {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "G": "home", "O": "end", "S": "delete", "I": "pageup", "Q": "pagedown",
                ";": "f1", "<": "f2", "=": "f3", ">": "f4", "?": "f5", "@": "f6",
                "A": "f7", "B": "f8", "C": "f9", "D": "f10", "\x85": "f11", "\x86": "f12",
            }.get(code)
            if key:
                return Event("key", key=key)
            # An unbound native scan code is not a printable command.
            return None
        if char != ESC:
            return self._plain_event(char)

        # Escape is both a key and the prefix for VT keyboard/mouse/focus reports.
        try:
            prefix = self._next_char(0.035)
        except queue.Empty:
            return Event("key", key="escape")
        if prefix not in {"[", "O"}:
            self._pending_input.appendleft(prefix)
            return Event("key", key="escape")
        self._ansi_buffer = prefix
        self._ansi_deadline = time.monotonic() + 0.25
        self._ansi_overflow = False
        return self._finish_vt_event()

    def _finish_vt_event(self) -> Event | None:
        # Retain split reports between frames. Unknown CSI finals still end the
        # packet, so focus-out/unsupported keys cannot consume following input.
        deadline = min(self._ansi_deadline, time.monotonic() + 0.035)
        while time.monotonic() < deadline:
            try:
                char = self._next_char(deadline - time.monotonic())
            except queue.Empty:
                break
            if char is None:
                self._ansi_buffer = ""
                return Event("eof")
            if char == ESC or not " " <= char <= "~":
                self._pending_input.appendleft(char)
                self._ansi_buffer = ""
                return None
            if len(self._ansi_buffer) < 128:
                self._ansi_buffer += char
            else:
                self._ansi_overflow = True
            if "@" <= char <= "~":
                sequence = self._ansi_buffer
                self._ansi_buffer = ""
                return None if self._ansi_overflow else self._vt_event(sequence)
        if time.monotonic() >= self._ansi_deadline:
            self._ansi_buffer = ""
        return None

    def _plain_event(self, char: str) -> Event | None:
        controls = {
            "\x01": "ctrl+a", "\x03": "ctrl+c", "\x04": "ctrl+d", "\x05": "ctrl+e",
            "\x08": "backspace", "\x09": "tab", "\x0b": "ctrl+k", "\x0c": "ctrl+l",
            "\x10": "ctrl+p", "\x12": "ctrl+r", "\x13": "ctrl+s", "\x14": "ctrl+t",
            "\x15": "ctrl+u", "\x16": "ctrl+v", "\x17": "ctrl+w", "\x19": "ctrl+y",
            "\x1b": "escape", "\x7f": "backspace", "\r": "enter", "\n": "enter",
        }
        if char in controls:
            return Event("key", key=controls[char])
        if char and char.isprintable():
            return Event("text", text=char, key=char.lower())
        return None

    def _vt_event(self, sequence: str) -> Event | None:
        if sequence.startswith("[") and sequence[1:2] == "<":
            match = re.fullmatch(r"<([0-9]+);([0-9]+);([0-9]+)([Mm])", sequence[1:])
            if not match:
                return None
            code, x, y, ending = match.groups()
            code_value, x_value, y_value = int(code), int(x) - 1, int(y) - 1
            if x_value < 0 or y_value < 0 or code_value & 128:
                return None
            modifiers = {"control": bool(code_value & 16), "shift": bool(code_value & 4)}
            if code_value & 64:
                if code_value & 2:  # Horizontal scrolling is not a vertical wheel.
                    return None
                return Event("mouse", x=x_value, y=y_value, action="wheel", wheel=1 if code_value & 1 == 0 else -1, **modifiers)
            motion = bool(code_value & 32)
            button = code_value & 3
            action = "release" if ending == "m" else "move" if motion else "release" if button == 3 else "press"
            return Event("mouse", x=x_value, y=y_value, button=button, action=action, **modifiers)
        if sequence == "[I":
            return Event("focus", action="in")
        if sequence == "[O":
            return Event("focus", action="out")
        if sequence.startswith("O"):
            key = {
                "OA": "up", "OB": "down", "OC": "right", "OD": "left", "OH": "home", "OF": "end",
                "OP": "f1", "OQ": "f2", "OR": "f3", "OS": "f4",
            }.get(sequence)
            return Event("key", key=key) if key else None
        if sequence.startswith("["):
            match = re.fullmatch(r"\[([0-9]*(?:;[0-9]+)?)([A-Z~])", sequence)
            if not match:
                return None
            code_text, final = match.groups()
            parts = code_text.split(";")
            modifiers = max(0, int(parts[-1]) - 1) if len(parts) > 1 else 0
            base = parts[0]
            key = {
                "A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
                "P": "f1", "Q": "f2", "R": "f3", "S": "f4", "Z": "tab",
                "1~": "home", "2~": "insert", "3~": "delete", "4~": "end", "5~": "pageup", "6~": "pagedown",
                "11~": "f1", "12~": "f2", "13~": "f3", "14~": "f4", "15~": "f5", "17~": "f6",
                "18~": "f7", "19~": "f8", "20~": "f9", "21~": "f10", "23~": "f11", "24~": "f12",
            }.get(base + final if final == "~" else final)
            if key:
                return Event("key", key=key, control=bool(modifiers & 4), shift=bool(modifiers & 1) or final == "Z")
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None and self._reader is not threading.current_thread() and self._reader.is_alive():
            self._reader.join(timeout=0.25)
        sequences = ""
        if self.mouse:
            sequences += CSI + "?1004l" + CSI + "?1006l" + CSI + "?1002l" + CSI + "?1000l"
        sequences += CSI + "?25h" + CSI + "0m" + CSI + "?1049l"
        try:
            sys.stdout.write(sequences)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        if os.name == "nt" and self._kernel32 is not None and self._console_modes:
            for handle, mode in reversed(self._console_modes):
                try:
                    self._kernel32.SetConsoleMode(handle, mode)
                except (AttributeError, OSError):
                    pass
        if os.name == "nt" and self._input_file_mode is not None:
            try:
                import msvcrt
                msvcrt.setmode(sys.stdin.fileno(), self._input_file_mode)
            except (OSError, ValueError):
                pass
        if self._termios_state:
            try:
                import termios
                fd, state = self._termios_state
                termios.tcsetattr(fd, termios.TCSADRAIN, state)
            except (ImportError, OSError, ValueError):
                pass
