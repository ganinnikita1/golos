"""Окно истории распознаваний.

Список всех фрагментов с поиском, копированием, повторной вставкой и очисткой.
Текст хранится в ядре (``controller.history()``), окно только отображает его и
перечитывает после каждого нового результата.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import formatting, icons, theme
from .widgets import Card, ElidedLabel, PageHeader, confirm

logger = logging.getLogger(__name__)


class HistoryWindow(QDialog):
    """Окно истории: таблица записей, поиск и действия с текстом."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._items: list = []
        self._filtered: list = []
        self.setWindowTitle("История распознаваний — Голос")
        self.setWindowIcon(icons.app_icon())
        self.resize(880, 640)
        self.setMinimumSize(760, 560)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ API
    def refresh(self) -> None:
        """Перечитать историю из ядра и перестроить таблицу."""
        try:
            items = list(self.controller.history() or [])
        except Exception as exc:
            logger.warning("Не удалось прочитать историю: %s", exc)
            items = []
        items.sort(key=lambda item: getattr(item, "ts", 0.0), reverse=True)
        self._items = items
        self._apply_filter()
        self._update_stats()

    # ------------------------------------------------------------ построение
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(16)
        header_row.addWidget(
            PageHeader(
                "История",
                "Все распознанные фрагменты. Текст можно скопировать или вставить заново.",
            ),
            1,
        )
        self.search_edit = QLineEdit(content)
        self.search_edit.setPlaceholderText("Поиск по тексту…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(240)
        self.search_edit.textChanged.connect(self._apply_filter)
        header_row.addWidget(self.search_edit, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header_row)

        self.stack = QStackedWidget(content)
        self.stack.addWidget(self._build_empty_page())
        self.stack.addWidget(self._build_table_page())
        layout.addWidget(self.stack, 1)

        self.status_label = ElidedLabel("", None, content)
        self.status_label.setMinimumHeight(20)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.copy_button = QPushButton("Копировать", content)
        self.copy_button.setObjectName("Primary")
        self.copy_button.clicked.connect(self._copy_selected)
        self.insert_button = QPushButton("Вставить в активное окно", content)
        self.insert_button.clicked.connect(self._insert_selected)
        self.clear_button = QPushButton("Очистить историю", content)
        self.clear_button.setObjectName("Danger")
        self.clear_button.clicked.connect(self._clear_history)
        button_row.addWidget(self.copy_button)
        button_row.addWidget(self.insert_button)
        button_row.addStretch(1)
        button_row.addWidget(self.clear_button)
        layout.addLayout(button_row)
        root.addWidget(content, 1)

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)
        self.empty_icon = _EmptyIcon(page)
        layout.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = QLabel("История пока пуста", page)
        self.empty_title.setObjectName("Heading")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.empty_title)
        self.empty_hint = QLabel(
            "Распознанный текст появится здесь после первой диктовки.",
            page,
        )
        self.empty_hint.setObjectName("Muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)
        layout.addStretch(1)
        return page

    def _build_table_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        card = Card()
        self.table = QTableWidget(0, 5, card)
        self.table.setHorizontalHeaderLabels(
            ["Время", "Текст", "Движок", "Длительность", "RTF"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        for column, alignment in (
            (0, Qt.AlignmentFlag.AlignLeft),
            (1, Qt.AlignmentFlag.AlignLeft),
            (2, Qt.AlignmentFlag.AlignLeft),
            (3, Qt.AlignmentFlag.AlignRight),
            (4, Qt.AlignmentFlag.AlignRight),
        ):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    alignment | Qt.AlignmentFlag.AlignVCenter
                )
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 70)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._copy_selected())
        card.add(self.table)

        preview_label = QLabel("Выбранный текст", card)
        preview_label.setObjectName("FieldLabel")
        card.add(preview_label)
        self.preview = QPlainTextEdit(card)
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(96)
        self.preview.setMaximumHeight(130)
        self.preview.setPlaceholderText("Выберите запись в списке, чтобы увидеть полный текст.")
        card.add(self.preview)
        layout.addWidget(card, 1)

        self.stats_label = QLabel("", page)
        self.stats_label.setObjectName("Stat")
        layout.addWidget(self.stats_label)
        return page

    # -------------------------------------------------------------- данные
    def _apply_filter(self) -> None:
        query = self.search_edit.text().strip().lower()
        if query:
            filtered = [
                item
                for item in self._items
                if query in (getattr(item, "text", "") or "").lower()
                or query in (getattr(item, "raw_text", "") or "").lower()
            ]
        else:
            filtered = list(self._items)
        self._filtered = filtered
        self._fill_table(filtered)
        has_any = bool(self._items)
        if filtered:
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)
            if has_any:
                self.empty_title.setText("Ничего не найдено")
                self.empty_hint.setText("Попробуйте изменить запрос или очистить поиск.")
            else:
                self.empty_title.setText("История пока пуста")
                self.empty_hint.setText(
                    "Распознанный текст появится здесь после первой диктовки."
                )
        self._update_buttons()
        self._update_stats()

    def _fill_table(self, items) -> None:
        self.table.setRowCount(0)
        for index, item in enumerate(items):
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                formatting.format_timestamp(getattr(item, "ts", 0.0)),
                getattr(item, "text", ""),
                formatting.engine_label(getattr(item, "engine", "")),
                formatting.format_duration(getattr(item, "duration_s", 0.0)),
                formatting.format_rtf(getattr(item, "rtf", 0.0)),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in {3, 4}:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 4:
                    cell.setForeground(QColor(theme.current().muted))
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, index)
                self.table.setItem(row, column, cell)
            self.table.setRowHeight(row, 44)
        if items:
            self.table.selectRow(0)

    def _on_selection_changed(self) -> None:
        item = self._selected_item()
        if item is None:
            self.preview.clear()
        else:
            self.preview.setPlainText(getattr(item, "text", ""))
        self._update_buttons()

    def _selected_item(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        anchor = self.table.item(row, 0)
        if anchor is None:
            return None
        index = anchor.data(Qt.ItemDataRole.UserRole)
        try:
            return self._filtered[int(index)]
        except (TypeError, ValueError, IndexError):
            return None

    def _update_buttons(self) -> None:
        has_selection = self._selected_item() is not None
        self.copy_button.setEnabled(has_selection)
        self.insert_button.setEnabled(has_selection)
        self.clear_button.setEnabled(bool(self._items))

    def _update_stats(self) -> None:
        total = len(self._items)
        seconds = sum(float(getattr(item, "duration_s", 0.0) or 0.0) for item in self._items)
        if not hasattr(self, "stats_label"):
            return
        if not total:
            self.stats_label.setText("")
            return
        parts = [formatting.records_label(total)]
        if seconds > 0:
            parts.append(formatting.minutes_label(seconds) + " речи")
        self.stats_label.setText(" · ".join(parts))

    def _status(self, text: str, role: str = "muted") -> None:
        palette = theme.current()
        color = {
            "danger": palette.danger,
            "success": palette.success,
            "warning": palette.warning,
            "muted": palette.muted,
        }.get(role, palette.muted)
        self.status_label.set_color(theme.qcolor(color))
        self.status_label.setText(text)
        self._status_token = getattr(self, "_status_token", 0) + 1
        token = self._status_token
        QTimer.singleShot(3500, lambda: self._clear_status(token))

    def _clear_status(self, token: int) -> None:
        if token == getattr(self, "_status_token", 0) and self.status_label.text():
            self.status_label.setText("")

    # -------------------------------------------------------------- действия
    def _copy_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        text = getattr(item, "text", "")
        try:
            self.controller.copy_to_clipboard(text)
            self._status("Текст скопирован в буфер обмена", "success")
        except Exception as exc:
            logger.warning("Не удалось скопировать текст: %s", exc)
            self._status(f"Не удалось скопировать текст: {exc}", "danger")

    def _insert_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        text = getattr(item, "text", "")
        if not text:
            return
        self._status("Вставляю текст в активное окно…", "muted")
        self.hide()
        QTimer.singleShot(220, lambda: self._do_insert(text))

    def _do_insert(self, text: str) -> None:
        try:
            inserted = self.controller.insert_text(text)
        except Exception as exc:
            logger.warning("Не удалось вставить текст: %s", exc)
            inserted = False
        if inserted is False:
            self.show()
            self.raise_()
            self.activateWindow()
            self._status("Не удалось вставить текст в активное окно", "danger")
        else:
            self._status("Текст вставлен", "success")

    def _clear_history(self) -> None:
        if not self._items:
            return
        if not confirm(
            self,
            "Очистить историю",
            "Все сохранённые распознавания будут удалены. Это действие нельзя отменить.",
            ok_text="Очистить",
            danger=True,
        ):
            return
        try:
            self.controller.clear_history()
        except Exception as exc:
            logger.exception("Не удалось очистить историю")
            self._status(f"Не удалось очистить историю: {exc}", "danger")
            return
        self.refresh()
        self._status("История очищена", "success")


class _EmptyIcon(QWidget):
    """Крупная полупрозрачная иконка микрофона для пустого состояния."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(88, 88)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        from . import painting

        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        background = theme.qcolor(palette.accent, 22)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, 88, 88), 24, 24)
        color = theme.qcolor(palette.accent, 150)
        painting.paint_mic(painter, QRectF(24, 20, 40, 48), color, width=2.2)
        painter.end()


__all__ = ["HistoryWindow"]
