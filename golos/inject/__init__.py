"""Вставка распознанного текста в активное окно."""

from __future__ import annotations

from .base import TextInjector, create_injector
from .injector import (
    ClipboardInjector,
    TypeInjector,
    get_clipboard_text,
    set_clipboard_text,
)

__all__ = [
    "TextInjector",
    "create_injector",
    "ClipboardInjector",
    "TypeInjector",
    "get_clipboard_text",
    "set_clipboard_text",
]
