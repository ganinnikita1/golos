"""Словарь замен пользователя: компиляция, применение, импорт/экспорт JSON.

Словарь — это список :class:`golos.config.DictionaryEntry`. Замены
регистронезависимые; ``whole_word=True`` (по умолчанию) требует совпадения
целого слова, иначе замена выполняется и как подстрока.

Всё написано на stdlib и работает с кириллицей/латиницей: в Python ``re``
``\\w`` Unicode-aware, поэтому ``\\b`` корректно работает для «ё», «Ж» и т.п.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from ..config import DictionaryEntry

logger = logging.getLogger(__name__)

#: Скомпилированная замена: (шаблон, чем заменять).
CompiledEntry = tuple[re.Pattern[str], str]


def _compile_one(entry: DictionaryEntry) -> CompiledEntry | None:
    source = (entry.source or "").strip()
    if not source:
        return None
    target = entry.target if entry.target is not None else ""
    escaped = re.escape(source)
    pattern = r"(?<!\w)" + escaped + r"(?!\w)" if entry.whole_word else escaped
    try:
        return re.compile(pattern, re.IGNORECASE | re.UNICODE), target
    except re.error:  # pragma: no cover - re.escape не даёт битых шаблонов
        logger.warning("Не удалось скомпилировать словарную замену: %r", source)
        return None


def compile_dictionary(entries: Iterable[DictionaryEntry]) -> list[CompiledEntry]:
    """Компилирует записи словаря в список (regex, замена).

    Записи с пустым ``source`` молча пропускаются. Порядок сохраняется —
    замены применяются последовательно, как задал пользователь.
    """
    compiled: list[CompiledEntry] = []
    for entry in entries or ():
        item = _compile_one(entry)
        if item is not None:
            compiled.append(item)
    return compiled


def apply_compiled(text: str, compiled: Sequence[CompiledEntry]) -> str:
    """Применяет уже скомпилированный словарь к тексту."""
    if not text or not compiled:
        return text
    for pattern, replacement in compiled:
        text = pattern.sub(replacement, text)
    return text


def apply_dictionary(text: str, entries: Iterable[DictionaryEntry] | Sequence[CompiledEntry]) -> str:
    """Применяет словарь замен.

    Принимает либо список :class:`DictionaryEntry` (скомпилирует на лету),
    либо уже скомпилированный список из :func:`compile_dictionary`.
    """
    if not text or not entries:
        return text
    first = entries[0]
    if isinstance(first, tuple):  # уже скомпилировано
        return apply_compiled(text, entries)  # type: ignore[arg-type]
    return apply_compiled(text, compile_dictionary(entries))  # type: ignore[arg-type]


def entries_from_dicts(items: Iterable[dict]) -> list[DictionaryEntry]:
    """Мягко превращает список dict (из UI/JSON) в записи словаря."""
    result: list[DictionaryEntry] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        result.append(
            DictionaryEntry(
                source=source,
                target=str(item.get("target") or ""),
                whole_word=bool(item.get("whole_word", True)),
            )
        )
    return result


def load_dictionary(path: str | Path) -> list[DictionaryEntry]:
    """Читает словарь из JSON. Битый/отсутствующий файл → пустой список.

    Формат: ``[{"source": "...", "target": "...", "whole_word": true}, ...]``
    либо объект ``{"entries": [...]}``.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("Не удалось прочитать файл словаря: %s", path)
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Файл словаря содержит некорректный JSON: %s", path)
        return []
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        return []
    return entries_from_dicts(data)


def save_dictionary(path: str | Path, entries: Iterable[DictionaryEntry]) -> Path:
    """Атомарно сохраняет словарь в JSON (UTF-8, без экранирования кириллицы)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [
            {
                "source": entry.source,
                "target": entry.target,
                "whole_word": bool(entry.whole_word),
            }
            for entry in entries or ()
            if (entry.source or "").strip()
        ],
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dict-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path
