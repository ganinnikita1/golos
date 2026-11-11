"""Файловые пути приложения с безопасным fallback.

Порядок выбора каталога данных:
1. явный override из окружения (GOLOS_APPDATA / GOLOS_LOCALAPPDATA) — удобно для тестов
   и портативного режима;
2. стандартный каталог Windows (%APPDATA%\\Golos, %LOCALAPPDATA%\\Golos);
3. если он недоступен на запись (корпоративные политики, песочница, read-only профиль) —
   %USERPROFILE%\\Golos;
4. в крайнем случае — каталог рядом с исполняемым файлом / пакетом.

Каталог выбирается один раз и кэшируется, чтобы конфиг и история всегда лежали вместе.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path

from . import APP_NAME

_lock = threading.RLock()
_cache: dict[str, Path] = {}


def _env_dir(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _user_home() -> Path:
    return Path.home()


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".golos-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve(kind: str) -> Path:
    """kind: 'appdata' | 'localappdata'."""
    with _lock:
        cached = _cache.get(kind)
        if cached is not None:
            return cached

        if kind == "appdata":
            explicit = _env_dir("GOLOS_APPDATA")
            base_env = os.environ.get("APPDATA")
            candidates = [
                explicit,
                Path(base_env) / APP_NAME if base_env else None,
                _user_home() / APP_NAME,
                _exe_dir() / "_data",
            ]
        else:
            explicit = _env_dir("GOLOS_LOCALAPPDATA")
            base_env = os.environ.get("LOCALAPPDATA")
            candidates = [
                explicit,
                Path(base_env) / APP_NAME if base_env else None,
                _user_home() / APP_NAME / "local",
                _exe_dir() / "_data" / "local",
            ]

        for candidate in candidates:
            if candidate is None:
                continue
            if _is_writable(candidate):
                _cache[kind] = candidate
                return candidate
        # Ничего не доступно — возвращаем первый непустой вариант, ошибка проявится позже.
        fallback = next((item for item in candidates if item is not None), Path.cwd() / APP_NAME)
        _cache[kind] = fallback
        return fallback


def appdata_dir() -> Path:
    """Конфиг, история, логи."""
    return _resolve("appdata")


def localappdata_dir() -> Path:
    """Модели и кэш (крупные файлы)."""
    return _resolve("localappdata")


def config_path() -> Path:
    return appdata_dir() / "config.json"


def history_path() -> Path:
    return appdata_dir() / "history.jsonl"


def logs_dir() -> Path:
    return appdata_dir() / "logs"


def models_dir() -> Path:
    return localappdata_dir() / "models"


def cache_dir() -> Path:
    return localappdata_dir() / "cache"


def assets_dir() -> Path:
    """Иконки и звуки, которые лежат рядом с пакетом."""
    return Path(__file__).resolve().parent / "assets"


def ensure_dirs() -> None:
    for path in (appdata_dir(), localappdata_dir(), logs_dir(), models_dir(), cache_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def describe() -> dict[str, str]:
    """Диагностика для логов и «О программе»."""
    return {
        "appdata": str(appdata_dir()),
        "localappdata": str(localappdata_dir()),
        "models": str(models_dir()),
        "logs": str(logs_dir()),
    }


#: Путь с именем пользователя: C:\Users\ima — не показываем в UI/скриншотах.
_USER_PATH_RE = re.compile(r"([A-Za-z]:\\Users\\)[^\\]+", re.IGNORECASE)


def display_path(value: str | Path) -> str:
    """Путь для показа пользователю (и на скриншотах) без имени учётной записи.

    Пример: ``C:\\Users\\ivan\\AppData\\Roaming\\Golos`` → ``%APPDATA%\\Golos``.
    Это же используется диагностикой, чтобы пользователь не публиковал свой
    домашний путь в issue.
    """
    text = str(value)
    replacements: list[tuple[str, str]] = []
    try:
        replacements.append((str(localappdata_dir()), "%LOCALAPPDATA%\\Golos"))
    except Exception:
        pass
    try:
        replacements.append((str(appdata_dir()), "%APPDATA%\\Golos"))
    except Exception:
        pass
    replacements.append((str(_user_home()), "%USERPROFILE%"))
    for base, token in replacements:
        if base and text.lower().startswith(base.lower()):
            text = token + text[len(base):]
            break
    return _USER_PATH_RE.sub(r"\1<user>", text)


def sanitize_for_report(text: str) -> str:
    """Убирает из текста отчёта/диагностики домашние пути и имя пользователя."""
    if not text:
        return text
    try:
        text = text.replace(str(_user_home()), "%USERPROFILE%")
    except Exception:
        pass
    return _USER_PATH_RE.sub(r"\1<user>", text)
