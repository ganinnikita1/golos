"""Проверка доставки горячей клавиши: реальный SendInput в сфокусированное окно.

Запускает окно-приёмник отдельным процессом, ставит глобальный хук в текущем
процессе и отправляет комбинацию. Печатает, дошла ли она до хука.

    python tools/hotkey_probe.py [--hotkey ctrl+alt+f11]
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from golos.config import HotkeyConfig  # noqa: E402
from golos.hotkey import create_listener, parse_hotkey  # noqa: E402
from tools.smoke_exe import INPUT, _key, VK  # noqa: E402

TARGET = ROOT / "tests" / "target_window.py"


def force_focus(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    user32.AllowSetForegroundWindow(-1)
    user32.ShowWindow(hwnd, 9)
    user32.SwitchToThisWindow(hwnd, True)
    user32.SetForegroundWindow(hwnd)
    return int(user32.GetForegroundWindow()) == hwnd


def vk_for(names: set[str]) -> list[int]:
    mapping = {
        "ctrl": VK["ctrl"], "alt": VK["alt"], "shift": VK["shift"], "win": VK["win"],
        "f11": VK["f11"], "f13": 0x7C, "space": 0x20,
    }
    keys: list[int] = []
    for name in ("ctrl", "alt", "shift", "win"):
        if name in names:
            keys.append(mapping[name])
    for name in names:
        if name not in {"ctrl", "alt", "shift", "win"}:
            if name not in mapping:
                return []
            keys.append(mapping[name])
    return keys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hotkey", default="ctrl+alt+f11")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="golos-hk-"))
    out = workdir / "target.txt"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    proc = subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), str(TARGET), str(out)])
    try:
        hwnd = 0
        deadline = time.time() + 15
        while time.time() < deadline:
            if out.exists():
                parts = out.read_text(encoding="utf-8").splitlines()
                if len(parts) > 1 and parts[1].strip():
                    hwnd = int(parts[1].strip())
                    break
            time.sleep(0.1)
        if not hwnd:
            print("окно-приёмник не поднялось")
            return 1
        focused = force_focus(hwnd)
        print("focus:", focused, "foreground:", hex(int(ctypes.windll.user32.GetForegroundWindow())))

        events: list[str] = []
        cfg = HotkeyConfig(hotkey=args.hotkey, mode="toggle", cancel_key="esc")
        listener = create_listener(cfg, on_start=lambda: events.append("start"), on_stop=lambda: events.append("stop"))
        listener.start()
        time.sleep(0.5)
        keys = vk_for(parse_hotkey(args.hotkey))
        print("keys:", [hex(k) for k in keys])

        def press(down: bool) -> int:
            seq = keys if down else list(reversed(keys))
            arr = (INPUT * len(seq))(*[_key(vk, up=not down) for vk in seq])
            return ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))

        print("send down:", press(True))
        time.sleep(0.25)
        print("send up:", press(False))
        time.sleep(0.8)
        print("events:", events)
        listener.stop()
        return 0 if events else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
