"""Контракт постобработки текста.

Пайплайн: сырой вывод ASR → postprocess() → (опционально) LLMPolisher → вставка.
Обе функции должны быть быстрыми (миллисекунды) и не падать на пустом тексте.

Реализация — в :mod:`golos.text.postprocess`; здесь сохранены контрактные
подписи, чтобы потребителям не приходилось знать про раскладку модулей.
"""

from __future__ import annotations

from typing import Protocol

from ..config import PolishConfig, TextConfig
from .postprocess import postprocess as _implemented_postprocess


def postprocess(text: str, cfg: TextConfig) -> str:
    """Детерминированная чистка: пунктуация, регистр, словарь, пробелы."""
    return _implemented_postprocess(text, cfg)


class Polisher(Protocol):
    """LLM-полировка (опционально)."""

    def is_configured(self) -> bool:
        """Есть ли ключ/URL, чтобы вообще пытаться."""

    def polish(self, text: str, *, context: str | None = None) -> str:
        """Возвращает улучшенный текст или исходный при любой проблеме."""
