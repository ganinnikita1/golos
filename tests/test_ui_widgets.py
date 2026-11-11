"""Тесты базовых виджетов интерфейса (offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from golos.ui import theme
from golos.ui.widgets import (
    Card,
    ElidedLabel,
    Field,
    HotkeyEdit,
    LevelMeter,
    PageHeader,
    Separator,
    ToggleSwitch,
)


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


def test_theme_stylesheet_has_no_unresolved_tokens(qapp):
    for name in ("dark", "light"):
        palette = theme.apply(qapp, name)
        stylesheet = qapp.styleSheet()
        assert palette.name == name
        assert "QPushButton" in stylesheet
        assert "$" not in stylesheet
        assert "Golos" not in stylesheet
    theme.apply(qapp, "dark")
    assert theme.current().name == "dark"


def test_build_palette_roles(qapp):
    theme.apply(qapp, "dark")
    palette = theme.build_palette(theme.current())
    assert palette.color(palette.ColorRole.Window).isValid()
    assert palette.color(palette.ColorRole.Highlight).isValid()


def test_toggle_switch_click(qapp):
    toggle = ToggleSwitch()
    toggle.show()
    wait(qapp)
    assert toggle.isChecked() is False
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    wait(qapp, 60)
    assert toggle.isChecked() is True
    toggle.setChecked(False)
    assert toggle.isChecked() is False
    toggle.close()


def test_level_meter(qapp):
    meter = LevelMeter(bars=12, bar_width=4, gap=3, height=24)
    meter.resize(240, 24)
    meter.show()
    wait(qapp)
    meter.set_level(0.8)
    wait(qapp, 160)
    assert meter.mode() == "level"
    meter.set_mode("pulse")
    wait(qapp, 100)
    assert meter.mode() == "pulse"
    meter.set_mode("idle")
    assert meter.mode() == "idle"
    meter.set_level(0.5)
    assert meter.mode() == "level"
    meter.set_role("danger")
    pixmap = meter.grab()
    assert not pixmap.isNull()
    meter.close()


def test_elided_label(qapp):
    label = ElidedLabel("Очень длинный текст, который должен обрезаться многоточием")
    label.resize(140, 24)
    label.set_color(QColor("#FFFFFF"))
    label.show()
    wait(qapp)
    assert label.text().startswith("Очень")
    assert not label.grab().isNull()
    label.close()


def test_hotkey_edit_records_sequence(qapp):
    edit = HotkeyEdit("ctrl+alt+space")
    edit.resize(220, 32)
    edit.show()
    edit.setFocus()
    wait(qapp)
    assert edit.text() == "Ctrl+Alt+Space"
    QTest.keyClick(
        edit,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    wait(qapp, 40)
    assert edit.spec() == "ctrl+shift+s"
    assert edit.text() == "Ctrl+Shift+S"
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    wait(qapp, 40)
    assert edit.spec() == ""
    edit.close()


def test_hotkey_edit_single_key_ignores_modifiers(qapp):
    edit = HotkeyEdit("esc", single_key=True)
    edit.show()
    edit.setFocus()
    wait(qapp, 40)
    QTest.keyClick(edit, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert edit.spec() == "esc"
    edit.close()


def test_layout_helpers(qapp):
    card = Card("Заголовок", "Подпись")
    field = Field("Поле", ElidedLabel("значение"), "Подсказка")
    card.add(field)
    card.add(Separator())
    header = PageHeader("Заголовок", "Подзаголовок")
    for widget in (card, header):
        widget.show()
        wait(qapp)
        assert not widget.grab().isNull()
        widget.close()
