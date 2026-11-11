"""Настройка логирования: консоль + ротируемый файл.

Каталог логов берётся из golos.paths (с учётом fallback), чтобы логи всегда лежали
рядом с конфигом и историей — даже если %APPDATA% недоступен на запись.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False


def setup(level: str = "INFO", log_dir: Path | None = None, *, console: bool | None = None) -> Path | None:
    """Инициализирует корневой логгер. Безопасно вызывать многократно."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        return None

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)-14s %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    if console is None:
        console = sys.stderr is not None and not getattr(sys, "frozen", False)
    if console:
        try:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(fmt)
            root.addHandler(stream)
        except Exception:
            pass

    log_path: Path | None = None
    try:
        if log_dir is None:
            from . import paths  # локальный импорт: paths не должен тянуть logging

            log_dir = paths.logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "golos.log"
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        log_path = None

    # Шумные библиотеки приглушаем.
    for name in ("urllib3", "huggingface_hub", "transformers", "filelock", "PIL", "matplotlib"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True
    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
