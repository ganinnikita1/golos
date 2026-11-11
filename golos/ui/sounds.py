"""Звуковые сигналы начала/конца записи.

Звуки синтезируются в ``golos/assets/sounds`` (см. ``golos.ui.assets_build``) и
проигрываются асинхронно через winsound, чтобы не блокировать интерфейс.
"""

from __future__ import annotations

import logging
import sys

from .. import paths

logger = logging.getLogger(__name__)

_SUPPORTED = sys.platform == "win32"


def play(name: str) -> None:
    """Проиграть короткий сигнал: 'start', 'stop' или 'error'."""
    if not _SUPPORTED:
        return
    path = paths.assets_dir() / "sounds" / f"{name}.wav"
    if not path.is_file():
        return
    try:
        import winsound

        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception as exc:  # звук не должен ломать диктовку
        logger.debug("Не удалось проиграть звук %s: %s", name, exc)
