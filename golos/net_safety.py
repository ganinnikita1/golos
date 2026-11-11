"""Проверка сетевых endpoint'ов перед отправкой ключей API.

По умолчанию разрешены только ``https``-адреса; ``http`` — лишь для локальных
серверов (LM Studio, Ollama, собственный шлюз на localhost). Это не даёт
случайно отправить API-ключ и текст по открытому каналу.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Хосты, для которых допустим http (локальные модели/шлюзы).
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


class UnsafeEndpointError(ValueError):
    """Endpoint небезопасен: ключ или данные ушли бы по открытому каналу."""


def validate_endpoint(url: str, *, purpose: str = "endpoint") -> str:
    """Проверяет URL и возвращает его без изменений. Бросает UnsafeEndpointError."""
    text = (url or "").strip()
    if not text:
        raise UnsafeEndpointError(f"Не задан адрес {purpose}. Укажите https://… в настройках.")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise UnsafeEndpointError(f"Некорректный адрес {purpose}: {text!r}") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeEndpointError(
            f"Адрес {purpose} должен начинаться с https:// (или http:// для локального сервера)."
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeEndpointError(f"В адресе {purpose} нет хоста: {text!r}")
    if parts.scheme == "http" and host not in LOCAL_HOSTS:
        raise UnsafeEndpointError(
            f"Небезопасный адрес {purpose}: {host}. API-ключ и данные нельзя отправлять "
            "по HTTP — используйте https:// (http разрешён только для localhost)."
        )
    return text
