"""Диагностика окружения: куда приложение пишет данные и работает ли логирование.

Запускать в НЕзапесоченном контексте (например, через WMI из pwsh), чтобы увидеть
поведение штатных путей %APPDATA%\\Golos / %LOCALAPPDATA%\\Golos.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from golos import paths  # noqa: E402
from golos.logging_setup import setup  # noqa: E402

out_file = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "diag.txt"
lines: list[str] = []


def add(message: str) -> None:
    lines.append(message)
    print(message, flush=True)


add(f"python: {sys.executable}")
add(f"frozen: {getattr(sys, 'frozen', False)}")
add(f"APPDATA={os.environ.get('APPDATA')}")
add(f"LOCALAPPDATA={os.environ.get('LOCALAPPDATA')}")
add(f"USERPROFILE={os.environ.get('USERPROFILE')}")
add(f"paths: {paths.describe()}")
add(f"config exists: {os.path.exists(paths.config_path())}")

probe = paths.appdata_dir() / "probe.txt"
try:
    probe.write_text("x", encoding="utf-8")
    add("appdata writable: yes")
    probe.unlink(missing_ok=True)
except OSError as exc:
    add(f"appdata writable: no ({exc})")

log_path = setup("INFO", console=False)
add(f"log file: {log_path}")
add(f"log exists: {bool(log_path and log_path.exists())}")

try:
    from golos.config import load_config

    cfg = load_config()
    add(f"config engine: {cfg.asr.engine}/{cfg.asr.gigaam_variant}, onboarding_done={cfg.onboarding_done}")
except Exception as exc:  # noqa: BLE001
    add(f"config load error: {exc}")

out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("DIAG DONE")
