"""Контракт вставки текста в активное окно Windows.

Реализация — в :mod:`golos.inject.injector`; здесь сохранена контрактная
подпись фабрики.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import InsertConfig
from .injector import create_injector as _implemented_create_injector


@runtime_checkable
class TextInjector(Protocol):
    def inject(self, text: str, cfg: InsertConfig) -> bool:
        """Вставляет текст в приложение, которое было активно до вызова.

        Возвращает True при успехе. Не должно бросать исключения наружу —
        пайплайн покажет ошибку пользователю.
        """


def create_injector(method: str = "clipboard") -> TextInjector:
    """method: clipboard | type. Неизвестный — clipboard."""
    return _implemented_create_injector(method)
