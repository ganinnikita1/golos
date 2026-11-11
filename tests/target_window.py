"""Окно-приёмник для e2e-тестов вставки (запускается отдельным процессом).

Создаёт окно с QLineEdit и каждые 100 мс пишет его содержимое в файл,
переданный первым аргументом, вместе с HWND окна (вторая строка).
Так тест может проверить, что Ctrl+V реально вставился в другое приложение,
а не в то же событийное пространство, где работает вставка.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget


def _force_foreground(window: QWidget) -> None:
    """Windows не даёт фоновому процессу просто так забрать фокус.

    SwitchToThisWindow обходит foreground-lock, поэтому окно-приёмник само
    настойчиво выходит на передний план, пока тест этого не увидит.
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        user32.SwitchToThisWindow(hwnd, True)
        user32.SetForegroundWindow(hwnd)
        window.activateWindow()
        window.raise_()
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: target_window.py <out_file>")
        return 2
    out = Path(sys.argv[1])

    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle("Golos E2E Target")
    window.resize(760, 140)
    field = QLineEdit(window)
    field.setGeometry(20, 50, 720, 44)
    field.setPlaceholderText("сюда вставляется диктовка")
    window.show()
    window.raise_()
    window.activateWindow()
    field.setFocus()

    def dump() -> None:
        try:
            out.write_text(f"{field.text()}\n{int(window.winId())}\n", encoding="utf-8")
        except OSError:
            pass

    def tick() -> None:
        dump()
        hwnd = int(window.winId())
        if int(ctypes.windll.user32.GetForegroundWindow()) != hwnd:
            _force_foreground(window)
        elif not field.hasFocus():
            # Окно активно, но фокус мог уйти с поля — возвращаем, иначе Ctrl+V не сработает.
            field.setFocus()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    dump()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
