"""Контракт движка распознавания речи.

Любой движок приводится к одному виду: принимает float32 mono PCM,
возвращает Transcript. Загрузку моделей/скачивание инкапсулирует движок.

ВАЖНО: движок обязан быть потокобезопасным на уровне одного вызова transcribe
(приложение зовёт его из одного рабочего потока) и не должен блокировать
импорт пакета: тяжёлые библиотеки (torch, ctranslate2) импортируются лениво
внутри load().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

import numpy as np

#: (сообщение для пользователя, прогресс 0..1 или None если неизвестен)
ProgressFn = Callable[[str, float | None], None]


@dataclass(frozen=True)
class Transcript:
    text: str
    engine: str = ""
    language: str = "ru"
    #: Длительность поданного аудио, сек.
    duration_s: float = 0.0
    #: Время распознавания, сек.
    elapsed_s: float = 0.0
    #: Сырой вывод модели до постобработки (для отладки/истории).
    raw: str | None = None

    @property
    def rtf(self) -> float:
        """Real-time factor: меньше 1 — быстрее реального времени."""
        if self.duration_s <= 0:
            return 0.0
        return self.elapsed_s / self.duration_s


@runtime_checkable
class ASREngine(Protocol):
    #: Короткое имя для UI и логов: "gigaam", "whisper", "cloud".
    name: str

    def load(self, progress: ProgressFn | None = None) -> None:
        """Готовит модель к работе. Идемпотентно."""

    def unload(self) -> None:
        """Освобождает память."""

    def is_loaded(self) -> bool: ...

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        """audio: float32 mono, значения примерно в [-1, 1]."""
