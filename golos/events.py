"""Шина событий между движком/пайплайном и UI.

Пайплайн живёт в фоновом потоке, Qt — в главном. Поэтому общение одностороннее:
продюсеры кладут события в потокобезопасную очередь, UI раз в ~30 мс забирает
всё накопленное через drain(). Никаких сигналов Qt из чужих потоков.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class EventBus:
    """Простая потокобезопасная шина: emit() из любого потока, drain() из UI."""

    def __init__(self, maxsize: int = 512) -> None:
        self._q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[Event], None]] = []

    def emit(self, type_: str, **data: Any) -> None:
        event = Event(type_, data)
        try:
            self._q.put_nowait(event)
        except queue.Full:
            # UI отстал — старые события не критичны, кроме ошибок.
            try:
                self._q.get_nowait()
                self._q.put_nowait(event)
            except (queue.Empty, queue.Full):
                pass
        if self._subscribers:
            with self._lock:
                subscribers = list(self._subscribers)
            for callback in subscribers:
                try:
                    callback(event)
                except Exception:  # подписчик не должен ломать продюсера
                    pass

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def drain(self, limit: int = 64) -> list[Event]:
        events: list[Event] = []
        for _ in range(limit):
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        return events


#: Общепринятые типы событий (строки, чтобы не плодить импорты).
STATE = "state"          # data: state in {idle, recording, transcribing, polishing, inserting, error}
LEVEL = "level"          # data: value 0..1 (уровень сигнала для оверлея)
PARTIAL = "partial"      # data: text (промежуточный/финальный текст для оверлея)
RESULT = "result"        # data: text, raw_text, elapsed_s, rtf, engine, inserted
ERROR = "error"          # data: message, detail
PROGRESS = "progress"    # data: message, fraction 0..1 (загрузка модели)
TOAST = "toast"          # data: message, level (info|warn|error)
