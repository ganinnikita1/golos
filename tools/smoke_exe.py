"""Проверка собранного exe: запуск, загрузка модели, горячая клавиша, запись.

Приложение запускается в portable-каталоге dist\\Golos\\_data (в песочнице штатные
%APPDATA% недоступны), модель копируется заранее. Горячая клавиша проверяется
реальным SendInput в сфокусированное окно-приёмник.

    python tools/smoke_exe.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "Golos" / "Golos.exe"
DATA = ROOT / "dist" / "Golos" / "_data"
LOG = DATA / "logs" / "golos.log"
TARGET = ROOT / "tests" / "target_window.py"

VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "f11": 0x7A, "space": 0x20}
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTunion)]


def _key(vk: int, up: bool = False) -> INPUT:
    inp = INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return inp


def press(keys: list[int], hold_ms: int = 60) -> int:
    inputs = (INPUT * len(keys))(*[_key(vk) for vk in keys])
    sent = ctypes.windll.user32.SendInput(len(keys), inputs, ctypes.sizeof(INPUT))
    time.sleep(hold_ms / 1000)
    ups = (INPUT * len(keys))(*[_key(vk, up=True) for vk in reversed(keys)])
    ctypes.windll.user32.SendInput(len(keys), ups, ctypes.sizeof(INPUT))
    return sent


def log_lines() -> list[str]:
    if not LOG.exists():
        return []
    return LOG.read_text(encoding="utf-8", errors="replace").splitlines()


def wait_for(needle: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(needle in line for line in log_lines()):
            return True
        time.sleep(0.5)
    return False


def focus_target() -> bool:
    workdir = Path(tempfile.mkdtemp(prefix="golos-smoke-"))
    out = workdir / "target.txt"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    proc = subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), str(TARGET), str(out)])
    hwnd = 0
    deadline = time.time() + 15
    while time.time() < deadline and not hwnd:
        if out.exists():
            parts = out.read_text(encoding="utf-8").splitlines()
            if len(parts) > 1 and parts[1].strip():
                hwnd = int(parts[1].strip())
        time.sleep(0.1)
    user32 = ctypes.windll.user32
    if hwnd:
        user32.AllowSetForegroundWindow(-1)
        user32.ShowWindow(hwnd, 9)
        user32.SwitchToThisWindow(hwnd, True)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)
    ok = bool(hwnd) and int(user32.GetForegroundWindow()) == hwnd
    # Процесс-приёмник не завершаем: он держит фокус на время проверки.
    return ok


def main() -> int:
    if not EXE.exists():
        print("exe не найден:", EXE)
        return 2

    print("=== фокус: окно-приёмник ===")
    print("focused:", focus_target())

    print("=== запуск exe (portable) ===")
    proc = subprocess.Popen([str(EXE), "--background"], cwd=str(EXE.parent),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    print("pid:", proc.pid)

    ok = wait_for("Модель загружена", 120)
    print("модель загружена:", ok)
    if not ok:
        print("\n".join(log_lines()[-15:]))
        proc.kill()
        return 1

    time.sleep(1.0)
    print("=== Ctrl+Alt+F11: старт записи ===")
    press([VK["ctrl"], VK["alt"], VK["f11"]])
    time.sleep(1.2)
    started = any("Запись начата" in line for line in log_lines())
    print("запись начата:", started)

    time.sleep(1.5)
    print("=== Ctrl+Alt+F11: стоп и распознавание ===")
    press([VK["ctrl"], VK["alt"], VK["f11"]])
    time.sleep(6.0)

    print("=== последние строки лога ===")
    print("\n".join(log_lines()[-12:]))
    proc.terminate()
    time.sleep(1)
    if proc.poll() is None:
        proc.kill()
    return 0 if started else 1


if __name__ == "__main__":
    sys.exit(main())
