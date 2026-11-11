"""Вставка прямым вводом (без буфера обмена).

Проверяем главное обещание режима «без буфера»: текст доходит до поля ввода,
а буфер обмена остаётся ровно таким, каким был. Окно создаём в своём процессе,
поэтому фокус и событийный цикл полностью под нашим контролем.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget  # noqa: E402

from golos.config import default_config  # noqa: E402
from golos.inject import create_injector  # noqa: E402
from golos.inject.injector import get_clipboard_text, set_clipboard_text  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_type_injector_does_not_touch_clipboard(qt_app) -> None:
    marker = "GOLOS-CLIPBOARD-SENTINEL-42"
    set_clipboard_text(marker, exclude_from_history=True)
    before = get_clipboard_text()

    window = QWidget()
    window.setWindowTitle("Golos direct-input test")
    window.resize(640, 120)
    field = QLineEdit(window)
    field.setGeometry(12, 32, 616, 40)
    window.show()
    window.raise_()
    window.activateWindow()
    field.setFocus()
    QTest.qWait(500)

    import ctypes

    if int(ctypes.windll.user32.GetForegroundWindow()) != int(window.winId()):
        window.close()
        pytest.skip("не удалось вывести тестовое окно на передний план")

    text = "Привет, это ввод без буфера. Числа 123 и «ёжик»."
    cfg = default_config()
    cfg.insert.trailing_space = False
    injector = create_injector("type")
    assert injector.inject(text, cfg.insert) is True
    QTest.qWait(300)

    assert field.text() == text
    assert get_clipboard_text() == before, "прямой ввод не должен менять буфер обмена"
    window.close()


def test_default_method_is_type() -> None:
    cfg = default_config()
    assert cfg.insert.method == "type"
    assert cfg.insert.exclude_from_history is True
    assert cfg.insert.restore_clipboard is True
