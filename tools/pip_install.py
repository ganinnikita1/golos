"""Обёртка над pip с вычищенным прокси-окружением.

Обёртка шелла заново прокидывает HTTPS_PROXY в дочерние процессы, поэтому
чистить переменные в PowerShell бесполезно — делаем это в Python перед запуском pip.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # Системный pip-кэш на этой машине недоступен на запись — держим свой.
    env["PIP_CACHE_DIR"] = os.path.join(ROOT, ".pip-cache")
    env["TMP"] = os.path.join(ROOT, ".tmp")
    env["TEMP"] = env["TMP"]
    os.makedirs(env["PIP_CACHE_DIR"], exist_ok=True)
    os.makedirs(env["TMP"], exist_ok=True)
    return env


def main(argv: list[str]) -> int:
    args = [PY, "-m", "pip", "install", "--no-input", *argv]
    print("+", " ".join(args), flush=True)
    return subprocess.call(args, env=clean_env())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
