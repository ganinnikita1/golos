"""Глобальная горячая клавиша через WH_KEYBOARD_LL."""

from __future__ import annotations

from .base import HotkeyListener, create_listener, parse_hotkey
from .hook import HotkeyController

__all__ = ["HotkeyListener", "HotkeyController", "create_listener", "parse_hotkey"]
