"""Контракт между UI и ядром приложения.

UI (PySide6) не знает, как устроены аудио/ASR/вставка: он получает объект,
удовлетворяющий протоколу Controller, и подписывается на шину событий.
Реализация — golos.app.DictationApp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .config import AppConfig
from .events import EventBus


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    is_default: bool = False
    channels: int = 1


@dataclass(frozen=True)
class HistoryItem:
    ts: float
    text: str
    raw_text: str = ""
    engine: str = ""
    duration_s: float = 0.0
    rtf: float = 0.0
    inserted: bool = False


@runtime_checkable
class Controller(Protocol):
    #: Единственная шина: UI читает её через QTimer + drain().
    bus: EventBus

    # --- конфигурация -----------------------------------------------------
    def get_config(self) -> AppConfig: ...

    def apply_config(self, cfg: AppConfig, *, save: bool = True) -> None:
        """Применяет настройки на лету (перезапускает хук/движок при надобности)."""

    # --- аудио ------------------------------------------------------------
    def get_devices(self) -> list[DeviceInfo]: ...

    def start_recording(self) -> None:
        """Начать запись (no-op если уже идёт)."""

    def stop_recording(self) -> None:
        """Остановить и запустить распознавание."""

    def cancel(self) -> None:
        """Отменить текущую запись/распознавание без вставки."""

    def is_busy(self) -> bool: ...

    # --- модель -----------------------------------------------------------
    def load_engine(self) -> None:
        """Асинхронно загружает/скачивает модель; прогресс идёт в bus."""

    def engine_status(self) -> dict:
        """{'engine': str, 'loaded': bool, 'loading': bool, 'message': str}"""

    # --- тексты -----------------------------------------------------------
    def history(self) -> list[HistoryItem]: ...

    def clear_history(self) -> None: ...

    def insert_text(self, text: str) -> bool: ...

    def copy_to_clipboard(self, text: str) -> None: ...

    # --- сервис -----------------------------------------------------------
    def open_config_dir(self) -> None: ...

    def quit(self) -> None: ...
