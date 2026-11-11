"""Запуск GUI-приложения отсоединённым процессом (для проверки собранного exe).

Обычный Start-Process в песочнице блокируется на пайпах stdio, пока GUI-процесс
жив, поэтому запускаем exe с DETACHED_PROCESS и NULL-потоками.

    python tools/launch_detached.py dist\\Golos\\Golos.exe --background
"""

from __future__ import annotations

import os
import subprocess
import sys

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: launch_detached.py <exe> [args...]")
        return 2
    exe = os.path.abspath(argv[0])
    proc = subprocess.Popen(
        [exe, *argv[1:]],
        cwd=os.path.dirname(exe),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )
    print(f"launched pid={proc.pid} exe={exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
