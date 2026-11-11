"""Проверка собранного бандла на утечку путей сборочной машины.

PyInstaller может затянуть в `_internal` служебные файлы (__pycache__, dist-info),
в которых зашит абсолютный путь сборки с именем пользователя. Такой файл в
публичном релизе — утечка PII. Скрипт сканирует dist и валит сборку при находке.

    python packaging/verify_bundle.py dist\\Golos
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

HOME = os.path.expanduser("~")
USER = getpass.getuser()
#: Маркеры, которых не должно быть в собранном бандле.
MARKERS = [
    HOME,
    f"\\Users\\{USER}",
    "/Users/" + USER,
    "deepseek-harness",
    "default-workspace",
]
SKIP_SUFFIXES = {".dll", ".pyd", ".so", ".exe"}
MAX_BYTES = 32 * 1024 * 1024


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path("dist") / "Golos"
    if not root.is_dir():
        print(f"нет каталога сборки: {root}")
        return 2
    bad: list[tuple[str, str]] = []
    checked = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
            if size > MAX_BYTES or size == 0:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        checked += 1
        text = data.replace(b"\x00", b"")
        for marker in MARKERS:
            if marker.encode("utf-8", "ignore") in text or marker.encode("utf-16-le", "ignore") in text:
                bad.append((str(path.relative_to(root)), marker))
                break
    print(f"проверено файлов: {checked}")
    if bad:
        print("НАЙДЕНЫ ПУТИ СБОРОЧНОЙ МАШИНЫ (утечка в публичном релизе):")
        for name, marker in bad[:20]:
            masked = marker[0] + "***" + marker[-1] if len(marker) > 3 else "***"
            print(f"  {name}  <- {masked}")
        return 1
    print("OK: путей сборочной машины в бандле нет")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
