"""Windows Terminal VT session, normalized input events, and cell compositor."""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import unicodedata
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
        cursor = max(0, x)
        raw = _safe_text(str(text))
        for char in raw:
            if self.ascii_only and ord(char) > 127:
                char = _ASCII.get(char, "?")
            width = cell_width(char)
            if width == 0:
                if cursor > 0 and cursor - 1 < self.width:
                    previous = self.cells[y][cursor - 1]
                    if not previous.continuation:
                        self.cells[y][cursor - 1] = Cell(previous.char + char, foreground, background, bold)
                continue
            if cursor < 0:
                cursor += width
                continue
            if cursor + width > limit:
                break
            self.cells[y][cursor] = Cell(char, foreground, background, bold)
            if width == 2:
                self.cells[y][cursor + 1] = Cell("", foreground, background, bold, True)
            cursor += width
        return cursor

    def fill(self, x: int, y: int, width: int, char: str = " ", foreground: str = "text.primary", background: str = "surface.base") -> None:
        if y < 0 or y >= self.height or width <= 0:
            return
        for column in range(max(0, x), min(self.width, x + width)):
            self.cells[y][column] = Cell(char[:1] or " ", foreground, background)

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
        category = unicodedata.category(char)
        if char == "\x1b" or category in {"Cc", "Cs"}:
            continue
        if char in "\r\n\t":
            cleaned.append(" ")
        else:
            cleaned.append(char)
    return "".join(cleaned)


class TerminalSession:
    def __init__(self, mouse: bool = True):
        self.mouse = mouse
        self._console_modes: list[tuple[Any, int]] = []
        self._input_file_mode: int | None = None
        self._kernel32: Any = None
        self._termios_state = None
        self._input_queue: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._closed = False
        self._ansi_buffer = ""

    def __enter__(self) -> "TerminalSession":
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
            for standard_handle, enable_bits in ((0xFFFFFFF6, 0x0200), (0xFFFFFFF5, 0x0004)):
                handle = kernel.GetStdHandle(standard_handle)
                mode = w.DWORD()
                if handle and kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    if kernel.SetConsoleMode(handle, mode.value | enable_bits):
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
                while not self._closed:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if ready:
                        value = os.read(fd, 1)
                        if not value:
                            continue
                        self._input_queue.put(value.decode("utf-8", errors="replace"))
        except (EOFError, OSError, ValueError):
            self._input_queue.put("\x04")

    def next_event(self, timeout: float = 0.08) -> Event | None:
        try:
            char = self._input_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if char in {"\x00", "\xe0"}:
            try:
                code = self._input_queue.get(timeout=0.05)
            except queue.Empty:
                return None
            return Event("key", key={
                "H": "up", "P": "down", "K": "left", "M": "right",
                "G": "home", "O": "end", "S": "delete", "I": "pageup", "Q": "pagedown",
                ";": "f1", "<": "f2", "=": "f3", ">": "f4", "?": "f5", "@": "f6",
            }.get(code, ""))
        if char != ESC:
            return self._plain_event(char)

        # Escape is both a key and the prefix for VT keyboard/mouse/focus reports.
        sequence = ""
        deadline = time.monotonic() + 0.035
        while time.monotonic() < deadline:
            try:
                next_char = self._input_queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            sequence += next_char
            if sequence.startswith("[") and sequence[-1:] in "ABCDEFGHIfmM~":
                break
            if sequence.startswith("O") and len(sequence) >= 2:
                break
        if not sequence:
            return Event("key", key="escape")
        return self._vt_event(sequence)

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
            if code_value & 64:
                return Event("mouse", x=x_value, y=y_value, action="wheel", wheel=1 if code_value & 1 == 0 else -1)
            motion = bool(code_value & 32)
            button = code_value & 3
            action = "move" if motion else "release" if ending == "m" or button == 3 else "press"
            return Event("mouse", x=x_value, y=y_value, button=button, action=action)
        if sequence == "[I":
            return Event("focus", action="in")
        if sequence == "[O":
            return Event("focus", action="out")
        if sequence.startswith("O"):
            return Event("key", key={"OP": "f1", "OQ": "f2", "OR": "f3", "OS": "f4"}.get("O" + sequence[1:], ""))
        if sequence.startswith("["):
            tail = sequence[1:]
            final = tail[-1:] if tail else ""
            code_text = tail[:-1]
            parts = code_text.split(";")
            modifiers = int(parts[-1]) - 1 if len(parts) > 1 and parts[-1].isdigit() else 0
            base = parts[0]
            key = {
                "A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
                "1~": "home", "2~": "insert", "3~": "delete", "4~": "end", "5~": "pageup", "6~": "pagedown",
                "11~": "f1", "12~": "f2", "13~": "f3", "14~": "f4", "15~": "f5", "17~": "f6",
            }.get(tail, {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}.get(final, ""))
            if base == "1" and final in "ABCD":
                key = {"A": "up", "B": "down", "C": "right", "D": "left"}[final]
            if key:
                return Event("key", key=key, control=modifiers == 4, shift=modifiers == 1)
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        sequences = ""
        if self.mouse:
            sequences += CSI + "?1004l" + CSI + "?1006l" + CSI + "?1002l" + CSI + "?1000l"
        sequences += CSI + "?25h" + CSI + "0m" + CSI + "?1049l"
        try:
            sys.stdout.write(sequences)
            sys.stdout.flush()
        except OSError:
            pass
        if os.name == "nt" and self._kernel32 is not None and self._console_modes:
            try:
                for handle, mode in reversed(self._console_modes):
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
            except (ImportError, OSError):
                pass
