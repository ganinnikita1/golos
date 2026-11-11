"""Тесты окна истории: список, поиск, копирование, очистка."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from golos.controller import HistoryItem
from golos.ui import history as history_module
from golos.ui.demo import FakeController
from golos.ui.history import HistoryWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def wait(app, milliseconds: int = 60) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    app.processEvents()


def make_window(qapp) -> tuple[HistoryWindow, FakeController]:
    controller = FakeController()
    window = HistoryWindow(controller)
    return window, controller


def test_history_rows_and_stats(qapp):
    window, controller = make_window(qapp)
    assert window.table.rowCount() == len(controller.history()) == 5
    assert "5 записей" in window.stats_label.text()
    window.close()


def test_search_filters_and_empty_state(qapp):
    window, _ = make_window(qapp)
    window.search_edit.setText("отчёт")
    wait(qapp)
    assert window.table.rowCount() == 1
    window.search_edit.setText("совершенно точно нет такого текста")
    wait(qapp)
    assert window.table.rowCount() == 0
    assert window.stack.currentIndex() == 0
    assert window.empty_title.text() == "Ничего не найдено"
    window.search_edit.setText("")
    wait(qapp)
    assert window.table.rowCount() == 5
    window.close()


def test_selection_preview_and_copy(qapp):
    window, _ = make_window(qapp)
    window.table.selectRow(0)
    wait(qapp)
    assert window.preview.toPlainText()
    window._copy_selected()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == window.preview.toPlainText()
    window.close()


def test_refresh_after_new_item(qapp):
    window, controller = make_window(qapp)
    controller._history.insert(
        0,
        HistoryItem(
            ts=time.time(),
            text="Новая короткая запись",
            raw_text="Новая короткая запись",
            engine="gigaam",
            duration_s=3.2,
            rtf=0.08,
            inserted=True,
        ),
    )
    window.refresh()
    assert window.table.rowCount() == 6
    assert window.table.item(0, 1).text() == "Новая короткая запись"
    window.close()


def test_clear_history(qapp, monkeypatch):
    window, controller = make_window(qapp)
    monkeypatch.setattr(history_module, "confirm", lambda *args, **kwargs: True)
    window._clear_history()
    assert window.table.rowCount() == 0
    assert controller.history() == []
    assert window.stack.currentIndex() == 0
    assert window.empty_title.text() == "История пока пуста"
    window.close()


def test_insert_selected_calls_controller(qapp, monkeypatch):
    window, controller = make_window(qapp)
    inserted: list[str] = []
    controller.insert_text = lambda text: inserted.append(text) or True  # type: ignore[method-assign]
    window.table.selectRow(0)
    wait(qapp)
    text = window.preview.toPlainText()
    assert text
    window._do_insert(text)
    assert inserted == [text]
    window.close()


def test_engine_labels_in_table(qapp):
    window, _ = make_window(qapp)
    labels = {window.table.item(row, 2).text() for row in range(window.table.rowCount())}
    assert "GigaAM-v3" in labels
    assert "faster-whisper" in labels
    window.close()
