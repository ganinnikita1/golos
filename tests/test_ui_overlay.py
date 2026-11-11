"""Тесты оверлея: флаги, состояния, анимации, отрисовка."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from golos.ui import overlay as overlay_module
from golos.ui.overlay import POSITIONS, OverlayWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def wait(app, milliseconds: int = 120) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    app.processEvents()


def make_overlay(qapp) -> OverlayWidget:
    widget = OverlayWidget()
    widget.show()
    wait(qapp, 40)
    return widget


def test_overlay_flags(qapp):
    widget = make_overlay(qapp)
    flags = widget.windowFlags()
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    widget.hide()
    widget.deleteLater()


def test_recording_state_and_content(qapp):
    widget = make_overlay(qapp)
    widget.start_recording()
    wait(qapp, 320)
    assert widget.isVisible()
    assert widget.mode == "recording"
    assert widget.opacity() > 0.9
    widget.set_level(0.75)
    widget.set_text("Проверка оверлея")
    wait(qapp, 120)
    assert widget.preview.text() == "Проверка оверлея"
    assert widget.meter.mode() == "level"
    image = widget.grab().toImage()
    assert not image.isNull()
    # Хотя бы один пиксель «пилюли» должен быть непрозрачным.
    opaque = 0
    for x in range(0, image.width(), 17):
        for y in range(0, image.height(), 11):
            if image.pixelColor(x, y).alpha() > 200:
                opaque += 1
                break
        if opaque:
            break
    assert opaque > 0
    widget.dismiss()
    wait(qapp, 260)
    assert not widget.isVisible()
    widget.deleteLater()


def test_busy_and_idle_states(qapp):
    widget = make_overlay(qapp)
    widget.start_recording()
    wait(qapp, 260)
    widget.set_state("transcribing")
    assert widget.mode == "busy"
    assert widget.meter.mode() == "pulse"
    widget.set_state("polishing")
    assert widget.mode == "busy"
    widget.set_state("idle")
    wait(qapp, 760)
    assert not widget.isVisible()
    widget.deleteLater()


def test_result_and_error_states(qapp, monkeypatch):
    monkeypatch.setattr(overlay_module, "RESULT_HIDE_MS", 120)
    monkeypatch.setattr(overlay_module, "ERROR_HIDE_MS", 120)
    widget = make_overlay(qapp)
    widget.start_recording()
    wait(qapp, 260)
    widget.show_result("Готовый текст", "Вставлено в активное окно")
    assert widget.mode == "result"
    assert widget.preview.text() == "Готовый текст"
    wait(qapp, 520)
    assert not widget.isVisible()

    widget.start_recording()
    wait(qapp, 260)
    widget.show_error("Микрофон не найден")
    assert widget.mode == "error"
    assert "Микрофон" in widget.preview.text()
    wait(qapp, 520)
    assert not widget.isVisible()
    widget.deleteLater()


def test_positions_stay_on_screen(qapp):
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()
    for position in POSITIONS:
        widget = OverlayWidget()
        widget.set_position(position)
        widget.start_recording()
        wait(qapp, 300)
        assert available.contains(widget.geometry()), f"оверлей вышел за экран: {position}"
        widget.dismiss()
        wait(qapp, 260)
        widget.deleteLater()


def test_overlay_does_not_activate(qapp):
    """Оверлей не должен активироваться (получать фокус) при показе."""
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("offscreen-платформа активирует все окна")
    widget = OverlayWidget()
    widget.start_recording()
    wait(qapp, 300)
    assert widget.isActiveWindow() is False
    widget.hide()
    widget.deleteLater()


def test_win32_transparent_styles(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("offscreen-платформа не создаёт HWND")
    if os.name != "nt":
        pytest.skip("Win32-стили проверяются только на Windows")
    import ctypes

    widget = OverlayWidget()
    widget.start_recording()
    wait(qapp, 320)
    hwnd = int(widget.winId())
    assert hwnd
    user32 = ctypes.windll.user32
    gwl_exstyle = -20
    style = int(user32.GetWindowLongW(hwnd, gwl_exstyle)) & 0xFFFFFFFF
    assert style & 0x08000000, "не выставлен WS_EX_NOACTIVATE"
    assert style & 0x00000020, "не выставлен WS_EX_TRANSPARENT"
    assert style & 0x00000080, "не выставлен WS_EX_TOOLWINDOW"
    widget.hide()
    widget.deleteLater()
