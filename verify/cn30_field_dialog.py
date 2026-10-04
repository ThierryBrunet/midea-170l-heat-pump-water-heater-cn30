#!/usr/bin/env python3
"""Field prompt for one keypad step at the water heater.

Run it in its own console. It shows the instruction and a one-second countdown,
and writes the same text to ~/.grok/waterheater-cue.json for the TUI status row.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

CUE = Path.home() / ".grok" / "waterheater-cue.json"

INSTRUCTION = (
    "GO TO THE WATER HEATER",
    "",
    "The heater is outside. Leave now.",
    "Do not press anything while the padlock is dark.",
    "",
    "At the panel:",
    "  Wait until the padlock lights by itself.",
    "  Then hold CANCEL for 3 seconds, once.",
    "  The padlock should go out.",
    "  Do not press any other key.",
    "",
    "Then come back to this computer.",
)


def render(left: int) -> str:
    lines = list(INSTRUCTION)
    lines.append("")
    lines.append(f"SECONDS LEFT  {max(left, 0)}")
    return "\n".join(lines) + "\n"


def write_cue(deadline: float) -> None:
    CUE.parent.mkdir(parents=True, exist_ok=True)
    CUE.write_text(
        json.dumps(
            {
                "deadline_epoch": deadline,
                "lines": [
                    "GO TO THE WATER HEATER. Do not press while the padlock is dark.",
                    "When the padlock lights, hold CANCEL for 3 seconds once.",
                    "Do not press any other key. Then come back.",
                ],
            }
        ),
        encoding="utf-8",
    )


def clear_cue() -> None:
    if CUE.exists():
        CUE.unlink()


def enable_vt_and_front() -> None:
    if sys.platform != "win32":
        return
    kernel = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel.SetConsoleTitleW("Water heater — go now")
    handle = kernel.GetStdHandle(-11)
    mode = ctypes.c_uint()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel.SetConsoleMode(handle, mode.value | 0x0004)
    hwnd = kernel.GetConsoleWindow()
    if not hwnd:
        return
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 60, 40, 920, 560, 0x0040)


def paint(text: str) -> None:
    if sys.platform == "win32":
        os.system("cls")
    else:
        sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(text)
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Water-heater field countdown")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.seconds < 1:
        print("REFUSED seconds must be >= 1")
        return 2
    if args.self_check:
        text = render(12)
        if "SECONDS LEFT  12" not in text or "hold CANCEL for 3 seconds" not in text:
            print("FAIL render")
            return 1
        print("SELF-CHECK ok")
        return 0

    enable_vt_and_front()
    deadline = time.time() + args.seconds
    shown = -1
    try:
        while True:
            left = int(deadline - time.time())
            if left < 0:
                break
            if left != shown:
                shown = left
                write_cue(deadline)
                paint(render(left))
            time.sleep(0.2)
        clear_cue()
        paint(
            "TIME IS UP\n\n"
            "Come back to the computer.\n"
            "Tell me whether the padlock went out.\n"
        )
        time.sleep(25)
    finally:
        clear_cue()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
