"""Тесты вставки текста: буфер обмена, восстановление форматов, SendInput.

Тесты с реальным окном требуют интерактивного рабочего стола; в offscreen/CI
они аккуратно пропускаются. Буфер обмена пользователя всегда сохраняется и
восстанавливается.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time

import pytest

from golos.config import InsertConfig
from golos.inject import _win32
from golos.inject.injector import (
    CLIPBOARD_CHUNK,
    ClipboardInjector,
    TypeInjector,
    _prepare,
    _split_text,
    create_injector,
    get_clipboard_text,
    set_clipboard_text,
)

CUSTOM_FORMAT_NAME = "GolosTestFormat"


# --------------------------------------------------------------------------- #
# Фикстуры
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clipboard_guard():
    """Сохраняет буфер обмена до теста и возвращает после."""
    snapshot = _win32.snapshot_clipboard()
    yield snapshot
    if snapshot is not None:
        _win32.restore_clipboard(snapshot)


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal"}:
        pytest.skip("QT_QPA_PLATFORM=offscreen: реальное окно недоступно")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip(f"Qt-платформа {app.platformName()!r}: нужен реальный Windows-дисплей")
    return app


def pump(app, seconds: float = 0.2) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def make_window(qt_app):
    """Создаёт окно, поднимает его на передний план или скипает тест."""
    from PySide6.QtWidgets import QLineEdit, QTextEdit

    created = []

    def factory(kind: str = "line"):
        widget = QLineEdit() if kind == "line" else QTextEdit()
        widget.resize(460, 120)
        widget.show()
        widget.raise_()
        widget.activateWindow()
        widget.setFocus()
        pump(qt_app, 0.35)
        if int(_win32.get_foreground_window()) != int(widget.winId()):
            widget.close()
            qt_app.processEvents()
            pytest.skip("не удалось вывести тестовое окно на передний план")
        created.append(widget)
        return widget

    yield factory
    for widget in created:
        widget.close()
    qt_app.processEvents()


def run_inject(qt_app, method: str, text: str, cfg: InsertConfig | None = None) -> bool:
    """Вызывает inject из рабочего потока, пока главный поток качает Qt-события."""
    cfg = cfg or InsertConfig()
    injector = create_injector(method)
    result: dict[str, object] = {}

    def work() -> None:
        result["ok"] = injector.inject(text, cfg)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while thread.is_alive() and time.time() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    thread.join(timeout=2)
    pump(qt_app, 0.2)
    return bool(result.get("ok"))


# --------------------------------------------------------------------------- #
# Фабрика
# --------------------------------------------------------------------------- #
def test_create_injector_methods() -> None:
    assert isinstance(create_injector(), ClipboardInjector)
    assert isinstance(create_injector("clipboard"), ClipboardInjector)
    assert isinstance(create_injector("CLIPBOARD"), ClipboardInjector)
    assert isinstance(create_injector("type"), TypeInjector)
    assert isinstance(create_injector("unknown"), ClipboardInjector)


def test_empty_text_is_noop(clipboard_guard) -> None:
    original = get_clipboard_text()
    assert ClipboardInjector().inject("", InsertConfig()) is True
    assert TypeInjector().inject("", InsertConfig()) is True
    assert get_clipboard_text() == original


# --------------------------------------------------------------------------- #
# Буфер обмена
# --------------------------------------------------------------------------- #
def test_set_and_get_clipboard_text(clipboard_guard) -> None:
    assert set_clipboard_text("Проверка буфера, ёжик 123")
    assert get_clipboard_text() == "Проверка буфера, ёжик 123"
    # Переводы строк в буфере — CRLF, обратно читаются как CRLF.
    assert set_clipboard_text("первая\nвторая")
    assert get_clipboard_text() == "первая\r\nвторая"


def _put_custom_clipboard(text: str, payload: bytes) -> int:
    fmt = _win32.user32.RegisterClipboardFormatW(CUSTOM_FORMAT_NAME)
    assert fmt
    assert _win32.open_clipboard()
    try:
        assert _win32.user32.EmptyClipboard()

        def put(data_format: int, data: bytes) -> None:
            handle = _win32.kernel32.GlobalAlloc(_win32.GMEM_MOVEABLE, len(data))
            assert handle
            pointer = _win32.kernel32.GlobalLock(handle)
            assert pointer
            ctypes.memmove(pointer, data, len(data))
            _win32.kernel32.GlobalUnlock(handle)
            assert _win32.user32.SetClipboardData(data_format, handle)

        put(_win32.CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")
        put(fmt, payload)
    finally:
        _win32.close_clipboard()
    return fmt


def _read_custom(fmt: int) -> bytes | None:
    assert _win32.open_clipboard()
    try:
        if not _win32.user32.IsClipboardFormatAvailable(fmt):
            return None
        return _win32._read_format(fmt)
    finally:
        _win32.close_clipboard()


def test_snapshot_restores_all_formats(clipboard_guard) -> None:
    payload = b"custom-golos-payload-\x01\x02\x03"
    fmt = _put_custom_clipboard("исходный текст", payload)
    snapshot = _win32.snapshot_clipboard()
    assert snapshot is not None
    assert any(item.fmt == fmt or item.name == CUSTOM_FORMAT_NAME for item in snapshot.items)

    assert set_clipboard_text("временный текст")
    assert get_clipboard_text() == "временный текст"

    assert _win32.restore_clipboard(snapshot)
    assert get_clipboard_text() == "исходный текст"
    restored = _read_custom(fmt)
    assert restored is not None
    assert restored.startswith(payload)


def test_copy_to_clipboard_and_read(clipboard_guard) -> None:
    assert ClipboardInjector.copy_to_clipboard("текст для копирования")
    assert ClipboardInjector.read_clipboard() == "текст для копирования"


# --------------------------------------------------------------------------- #
# Подготовка текста и нарезка
# --------------------------------------------------------------------------- #
def test_prepare_normalizes_newlines_and_nul() -> None:
    assert _prepare("a\r\nb\rc") == "a\nb\nc"
    assert _prepare("a\x00b") == "ab"
    assert _prepare(None) == ""  # type: ignore[arg-type]


def test_split_text_short_is_single_chunk() -> None:
    assert _split_text("короткий текст") == ["короткий текст"]
    assert _split_text("") == []


def test_split_text_long_preserves_content() -> None:
    text = ("Слово " * 4000).strip()  # ~30 000 символов
    chunks = _split_text(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= CLIPBOARD_CHUNK for chunk in chunks)


def test_split_text_does_not_break_surrogate_pairs() -> None:
    text = "😀" * 6000  # 12 000 UTF-16 code units
    chunks = _split_text(text)
    assert "".join(chunks) == text
    for chunk in chunks:
        assert not ("\ud800" <= chunk[-1] <= "\udbff"), "чанк не должен кончаться старшим суррогатом"
        assert not ("\udc00" <= chunk[0] <= "\udfff"), "чанк не должен начинаться младшим суррогатом"


# --------------------------------------------------------------------------- #
# Реальная вставка в собственное окно (SendInput)
# --------------------------------------------------------------------------- #
def test_clipboard_inject_into_window(qt_app, make_window, clipboard_guard) -> None:
    window = make_window("line")
    text = "Привет, это тест вставки 123. Ёжик — «Голос»!"
    assert run_inject(qt_app, "clipboard", text)
    assert window.text() == text


def test_clipboard_inject_without_restore_flag(qt_app, make_window) -> None:
    window = make_window("line")
    text = "текст без восстановления буфера"
    cfg = InsertConfig(restore_clipboard=False, paste_delay_ms=60)
    assert run_inject(qt_app, "clipboard", text, cfg)
    assert window.text() == text


def test_type_inject_into_window(qt_app, make_window, clipboard_guard) -> None:
    window = make_window("line")
    original = get_clipboard_text()
    assert run_inject(qt_app, "type", "Съешь ещё этих мягких булок")
    assert window.text() == "Съешь ещё этих мягких булок"
    assert get_clipboard_text() == original  # type не трогает буфер


def test_press_enter_after(qt_app, make_window, clipboard_guard) -> None:
    window = make_window("text")
    cfg = InsertConfig(press_enter_after=True, paste_delay_ms=60)
    assert run_inject(qt_app, "clipboard", "строка", cfg)
    assert window.toPlainText() == "строка\n"


def test_long_text_injected_in_chunks(qt_app, make_window, clipboard_guard) -> None:
    window = make_window("line")
    text = "".join(f"фраза{index} " for index in range(1800)).strip()  # ~20 000 символов
    assert len(text) > CLIPBOARD_CHUNK
    assert run_inject(qt_app, "clipboard", text)
    assert window.text() == text


# --------------------------------------------------------------------------- #
# Логика восстановления буфера (детерминированно, без реального буфера)
# --------------------------------------------------------------------------- #
class FakeWin32:
    """Подменяет win32-примитивы, чтобы проверить последовательность вызовов."""

    def __init__(self, *, clipboard_text: str | None, foregrounds: list[int] | None = None) -> None:
        self.snapshot = object()
        self.clipboard_text = clipboard_text
        self.foregrounds = list(foregrounds or [0])
        self.calls: list[tuple] = []
        self.chunks: list[str] = []

    # --- API, которое использует injector ------------------------------ #
    def get_foreground_window(self) -> int:
        self.calls.append(("foreground",))
        return self.foregrounds.pop(0) if len(self.foregrounds) > 1 else self.foregrounds[0]

    def snapshot_clipboard(self):
        self.calls.append(("snapshot",))
        return self.snapshot

    def set_clipboard_text(self, text: str, **kwargs) -> bool:
        self.calls.append(("set", text, kwargs))
        self.chunks.append(text)
        return True

    def press_ctrl_v(self) -> bool:
        self.calls.append(("ctrl_v",))
        return True

    def get_clipboard_text(self):
        self.calls.append(("get",))
        return self.clipboard_text

    def restore_clipboard(self, snapshot, **kwargs) -> bool:
        self.calls.append(("restore", snapshot, kwargs))
        return True

    def clear_clipboard(self) -> bool:
        self.calls.append(("clear",))
        self.clipboard_text = None
        return True

    def press_enter(self) -> bool:
        self.calls.append(("enter",))
        return True


def _fake_module(monkeypatch, fake: FakeWin32):
    import golos.inject.injector as injector_module

    monkeypatch.setattr(injector_module, "_win32", fake)
    monkeypatch.setattr(injector_module, "CONSUME_DELAY_S", 0.0)
    monkeypatch.setattr(injector_module, "RESTORE_DELAY_S", 0.0)
    monkeypatch.setattr(injector_module, "CHUNK_GAP_S", 0.0)
    monkeypatch.setattr(injector_module.ClipboardInjector, "restore_delay_s", 0.0)
    return injector_module


def test_restore_happens_when_clipboard_holds_our_text(monkeypatch) -> None:
    fake = FakeWin32(clipboard_text="наш текст")
    injector_module = _fake_module(monkeypatch, fake)
    assert injector_module.ClipboardInjector().inject("наш текст", InsertConfig(paste_delay_ms=0))
    kinds = [call[0] for call in fake.calls]
    assert kinds[0] == "foreground"
    assert "snapshot" in kinds
    assert "ctrl_v" in kinds
    assert "restore" in kinds


def test_restore_skipped_when_clipboard_changed_by_user(monkeypatch) -> None:
    fake = FakeWin32(clipboard_text="пользователь скопировал картинку-текст")
    injector_module = _fake_module(monkeypatch, fake)
    assert injector_module.ClipboardInjector().inject("наш текст", InsertConfig(paste_delay_ms=0))
    kinds = [call[0] for call in fake.calls]
    assert "ctrl_v" in kinds
    assert "restore" not in kinds


def test_no_snapshot_when_restore_disabled(monkeypatch) -> None:
    fake = FakeWin32(clipboard_text="что угодно")
    injector_module = _fake_module(monkeypatch, fake)
    cfg = InsertConfig(restore_clipboard=False, paste_delay_ms=0)
    assert injector_module.ClipboardInjector().inject("наш текст", cfg)
    kinds = [call[0] for call in fake.calls]
    assert "snapshot" not in kinds
    assert "restore" not in kinds


def test_multichunk_restores_once_after_last_chunk(monkeypatch) -> None:
    text = "слово " * 3000  # 18 000 символов → несколько чанков
    fake = FakeWin32(clipboard_text=text[-CLIPBOARD_CHUNK:])
    # get_clipboard_text должен вернуть последний чанк — подстроим после set.
    fake.get_clipboard_text = lambda: fake.chunks[-1]  # type: ignore[method-assign]
    injector_module = _fake_module(monkeypatch, fake)
    assert injector_module.ClipboardInjector().inject(text, InsertConfig(paste_delay_ms=0))
    kinds = [call[0] for call in fake.calls]
    assert kinds.count("set") >= 2
    assert kinds.count("ctrl_v") == kinds.count("set")
    assert kinds.count("restore") == 1
    # Восстановление — после последней вставки.
    last_ctrl_v = len(kinds) - 1 - kinds[::-1].index("ctrl_v")
    assert kinds.index("restore") > last_ctrl_v


def test_focus_change_aborts_insertion(monkeypatch) -> None:
    fake = FakeWin32(clipboard_text=None, foregrounds=[111, 222])
    injector_module = _fake_module(monkeypatch, fake)
    assert injector_module.ClipboardInjector().inject("текст", InsertConfig(paste_delay_ms=0)) is False
    kinds = [call[0] for call in fake.calls]
    assert "ctrl_v" not in kinds
    assert "restore" not in kinds
