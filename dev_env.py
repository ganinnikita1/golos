"""Подготовка изолированной среды для запуска и тестов, когда стандартные
каталоги Windows (%APPDATA%/%%LOCALAPPDATA%) недоступны на запись.

В этом окружении Python-процессы могут писать только внутри workspace,
поэтому реальные %APPDATA%\\Golos и %LOCALAPPDATA%\\Golos недоступны.
Для разработки перенаправляем их в repos/golos/.devdata.

Использование:
    python dev_env.py -- <команда ...>        # запустить команду с env
    python dev_env.py --show                  # показать каталоги
    from dev_env import apply; apply()        # внутри тестов/conftest
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV_DATA = ROOT / ".devdata"


def apply() -> dict[str, str]:
    """Прописывает GOLOS_* в текущий процесс и возвращает env-словарь."""
    ap = DEV_DATA / "appdata"
    local = DEV_DATA / "localappdata"
    hf = DEV_DATA / "hf"
    hf_hub = hf / "hub"
    for path in (ap, local, ap / "logs", local / "models", local / "cache", hf, hf_hub):
        path.mkdir(parents=True, exist_ok=True)
    env = {
        "GOLOS_APPDATA": str(ap),
        "GOLOS_LOCALAPPDATA": str(local),
        # transformers/huggingface_hub требуют writable кэш; в песочнице ~/.cache недоступен
        "HF_HOME": str(hf),
        "HF_HUB_CACHE": str(hf_hub),
        "HUGGINGFACE_HUB_CACHE": str(hf_hub),
        "TRANSFORMERS_CACHE": str(hf_hub),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    os.environ.update(env)
    return env


def main(argv: list[str]) -> int:
    apply()
    if not argv or argv[0] == "--show":
        print(f"GOLOS_APPDATA={os.environ['GOLOS_APPDATA']}")
        print(f"GOLOS_LOCALAPPDATA={os.environ['GOLOS_LOCALAPPDATA']}")
        return 0
    if argv[0] == "--":
        argv = argv[1:]
    print("+", " ".join(argv), file=sys.stderr)
    return subprocess.call(argv, env=dict(os.environ))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
