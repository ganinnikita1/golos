"""История распознаваний: JSONL в %APPDATA%\\Golos\\history.jsonl.

Храним последние N записей (по умолчанию 200). Файл перезаписывается
атомарно при обрезке, дописывается построчно при новой записи.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from . import paths
from .controller import HistoryItem
from .logging_setup import get_logger

logger = get_logger(__name__)


class HistoryStore:
    def __init__(self, path: Path | None = None, limit: int = 200) -> None:
        self.path = path or paths.history_path()
        self.limit = max(10, int(limit))
        self._lock = threading.RLock()
        self._items: list[HistoryItem] | None = None

    # ------------------------------------------------------------------ #
    def _load(self) -> list[HistoryItem]:
        items: list[HistoryItem] = []
        try:
            with self.path.open("r", encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        items.append(
                            HistoryItem(
                                ts=float(data.get("ts", 0.0)),
                                text=str(data.get("text", "")),
                                raw_text=str(data.get("raw_text", "")),
                                engine=str(data.get("engine", "")),
                                duration_s=float(data.get("duration_s", 0.0)),
                                rtf=float(data.get("rtf", 0.0)),
                                inserted=bool(data.get("inserted", False)),
                            )
                        )
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Не удалось прочитать историю")
        items.sort(key=lambda item: item.ts)
        return items[-self.limit:]

    def _ensure_loaded(self) -> list[HistoryItem]:
        if self._items is None:
            self._items = self._load()
        return self._items

    # ------------------------------------------------------------------ #
    def add(self, item: HistoryItem) -> None:
        with self._lock:
            items = self._ensure_loaded()
            items.append(item)
            if len(items) > self.limit:
                items = items[-self.limit:]
                self._items = items
                self._rewrite(items)
            else:
                self._append(item)

    def _append(self, item: HistoryItem) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(item), ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Не удалось дописать историю")

    def _rewrite(self, items: list[HistoryItem]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".history-", suffix=".jsonl")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for item in items:
                    fh.write(json.dumps(dataclasses.asdict(item), ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            logger.exception("Не удалось перезаписать историю")

    def items(self) -> list[HistoryItem]:
        """Новые сверху."""
        with self._lock:
            return list(reversed(self._ensure_loaded()))

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._rewrite([])

    def add_text(self, text: str, *, raw_text: str = "", engine: str = "", duration_s: float = 0.0,
                 rtf: float = 0.0, inserted: bool = False) -> HistoryItem:
        item = HistoryItem(
            ts=time.time(),
            text=text,
            raw_text=raw_text,
            engine=engine,
            duration_s=duration_s,
            rtf=rtf,
            inserted=inserted,
        )
        self.add(item)
        return item
