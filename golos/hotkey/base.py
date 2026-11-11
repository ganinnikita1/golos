"""Контракт глобальной горячей клавиши.

Реализация — низкоуровневый хук клавиатуры (WH_KEYBOARD_LL) в отдельном потоке
с собственным message loop. Никаких сторонних пакетов.

Режимы:
* toggle:        on_start() при первом нажатии, on_stop() при втором.
* push_to_talk:  on_start() при нажатии, on_stop() при отпускании.
* cancel_key (например Esc) → on_cancel() в любом состоянии.

``state_getter`` — необязательный колбэк, по которому слушатель понимает,
идёт ли сейчас запись/распознавание: только в этом состоянии Esc перехватывается
(``on_cancel``) и не доходит до активного приложения.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from ..config import HotkeyConfig
from .hook import create_listener as _implemented_create_listener
from .hook import parse_hotkey as _implemented_parse_hotkey


@runtime_checkable
class HotkeyListener(Protocol):
    def start(self) -> None:
        """Ставит хук. Не блокирует."""

    def stop(self) -> None:
        """Снимает хук и завершает поток."""

    @property
    def is_running(self) -> bool: ...


def parse_hotkey(spec: str) -> frozenset[str]:
    """'ctrl+alt+space' -> {'ctrl', 'alt', 'space'} (нормализованные имена)."""
    return _implemented_parse_hotkey(spec)


def create_listener(
    cfg: HotkeyConfig,
    *,
    on_start: Callable[[], None],
    on_stop: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    state_getter: Callable[[], bool] | None = None,
) -> HotkeyListener:
    return _implemented_create_listener(
        cfg,
        on_start=on_start,
        on_stop=on_stop,
        on_cancel=on_cancel,
        state_getter=state_getter,
    )
