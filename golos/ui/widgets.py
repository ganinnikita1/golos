"""Переиспользуемые виджеты интерфейса «Голоса».

Здесь собраны строительные блоки, из которых собираются окна: карточки,
переключатели, поля с подписями, индикатор уровня, редактор горячих клавиш.
Всё рисуется вручную по палитре из golos.ui.theme, чтобы интерфейс не
выглядел как стандартные контролы Qt.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Property,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import formatting, theme


# --------------------------------------------------------------------------- #
# Карточки, заголовки, подписи
# --------------------------------------------------------------------------- #
class Card(QFrame):
    """Карточка-контейнер: поверхность, рамка, скругление 12 px."""

    def __init__(self, title: str | None = None, subtitle: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.GAP, theme.GAP, theme.GAP, theme.GAP)
        outer.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            outer.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("FieldHint")
            subtitle_label.setWordWrap(True)
            outer.addWidget(subtitle_label)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body)
        outer.addStretch(1)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class Field(QWidget):
    """Подпись сверху + контрол + необязательная подсказка снизу."""

    def __init__(
        self,
        label: str,
        widget: QWidget | None = None,
        hint: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        title = QLabel(label)
        title.setObjectName("FieldLabel")
        layout.addWidget(title)
        self.widget = widget
        if widget is not None:
            layout.addWidget(widget)
        self.hint_label: QLabel | None = None
        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("FieldHint")
            self.hint_label.setWordWrap(True)
            layout.addWidget(self.hint_label)

    def set_hint(self, text: str) -> None:
        if self.hint_label is None:
            self.hint_label = QLabel(text)
            self.hint_label.setObjectName("FieldHint")
            self.hint_label.setWordWrap(True)
            self.layout().addWidget(self.hint_label)
        self.hint_label.setText(text)


class PageHeader(QWidget):
    """Крупный заголовок вкладки с пояснением."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("Title")
        layout.addWidget(title_label)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("Subtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        if not subtitle:
            self.subtitle.hide()


class Separator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Separator")
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFixedHeight(1)


class Stat(QLabel):
    """Мелкая второстепенная строка статистики."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Stat")


# --------------------------------------------------------------------------- #
# Переключатель
# --------------------------------------------------------------------------- #
class ToggleSwitch(QAbstractButton):
    """Аккуратный переключатель 46×26 с анимацией ручки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = max(0.0, min(1.0, float(value)))
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt API
        if checked == self.isChecked():
            return
        super().setChecked(checked)
        self._anim.stop()
        self._offset = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = track.height() / 2
        enabled = self.isEnabled()
        if self.isChecked():
            fill = theme.qcolor(palette.accent)
        else:
            fill = theme.qcolor(palette.surface_alt)
        if not enabled:
            fill.setAlpha(90)
        border = theme.qcolor(palette.accent if self.isChecked() else palette.border_strong)
        if self.hasFocus():
            border = theme.qcolor(palette.accent)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawRoundedRect(track, radius, radius)

        knob_d = self.height() - 6
        x = 3 + self._offset * (self.width() - knob_d - 6)
        knob = QRectF(x, 3, knob_d, knob_d)
        knob_color = QColor("#FFFFFF")
        if not enabled:
            knob_color.setAlpha(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob)
        painter.end()


# --------------------------------------------------------------------------- #
# Индикатор уровня
# --------------------------------------------------------------------------- #
class LevelMeter(QWidget):
    """Живой индикатор уровня: набор столбиков с плавным сглаживанием."""

    def __init__(
        self,
        bars: int = 24,
        bar_width: int = 4,
        gap: int = 3,
        height: int = 30,
        parent=None,
    ):
        super().__init__(parent)
        self._bars = max(3, int(bars))
        self._bar_width = max(2, int(bar_width))
        self._gap = max(1, int(gap))
        self._height = max(8, int(height))
        self.setMinimumSize(self.sizeHint())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._history: list[float] = [0.0] * self._bars
        self._target = 0.0
        self._level = 0.0
        self._phase = 0.0
        self._mode = "idle"  # idle | level | pulse
        self._role = "accent"  # accent | danger | muted
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(self._height)

    # --- API ---------------------------------------------------------------
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(
            self._bars * self._bar_width + (self._bars - 1) * self._gap,
            self._height,
        )

    def set_active(self, active: bool) -> None:
        if active and self._mode == "idle":
            self._mode = "level"
        if active:
            self._timer.start()
        elif self._mode == "level":
            self._mode = "idle"
            self._target = 0.0
            self._timer.stop()
            self._decay_all()
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode not in {"idle", "level", "pulse"}:
            return
        self._mode = mode
        if mode == "pulse":
            self._timer.start()
        elif mode == "idle":
            self._timer.stop()
            self._target = 0.0
            self._decay_all()
        else:
            self._timer.start()
        self.update()

    def mode(self) -> str:
        return self._mode

    def set_role(self, role: str) -> None:
        self._role = role if role in {"accent", "danger", "muted"} else "accent"
        self.update()

    def set_level(self, value: float) -> None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        self._target = max(0.0, min(1.0, value))
        if self._mode == "idle" and self._target > 0.01:
            self.set_active(True)

    # --- внутреннее --------------------------------------------------------
    def _decay_all(self) -> None:
        self._history = [0.0] * self._bars
        self._level = 0.0

    def _tick(self) -> None:
        if self._mode == "pulse":
            self._phase += 0.12
            self.update()
            return
        self._level += (self._target - self._level) * 0.45
        self._target *= 0.82
        self._history = self._history[1:] + [self._level]
        if self._level < 0.005 and self._target < 0.005:
            self._level = 0.0
            self._target = 0.0
        self.update()

    def _bar_color(self, painter: QPainter, palette) -> QColor:
        if self._role == "danger":
            color = theme.qcolor(palette.danger)
        elif self._role == "muted":
            color = theme.qcolor(palette.muted)
        else:
            color = theme.qcolor(palette.accent)
        return color

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = self._bar_color(painter, palette)
        count = self._bars
        slot = self.width() / float(count)
        mid = self.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(count):
            if self._mode == "pulse":
                value = 0.22 + 0.22 * math.sin(self._phase + index * 0.55)
                value += 0.1 * math.sin(self._phase * 0.7 + index * 1.3)
                value = max(0.05, min(1.0, value))
            elif self._mode == "level":
                value = max(0.04, self._history[index])
            else:
                value = 0.05
            color = QColor(base)
            if self._mode == "idle":
                color.setAlpha(40)
            else:
                color.setAlpha(120 + int(120 * min(1.0, value)))
            bar_height = max(2.0, value * (self.height() - 4))
            rect = QRectF(
                index * slot + (slot - self._bar_width) / 2.0,
                mid - bar_height / 2.0,
                float(self._bar_width),
                bar_height,
            )
            painter.setBrush(color)
            painter.drawRoundedRect(rect, self._bar_width / 2.0, self._bar_width / 2.0)
        painter.end()


# --------------------------------------------------------------------------- #
# Мигающая точка статуса
# --------------------------------------------------------------------------- #
class StatusDot(QWidget):
    """Цветная точка с мягким пульсом (идёт запись / идёт загрузка)."""

    def __init__(self, size: int = 8, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(theme.current().accent)
        self._phase = 0.0
        self._pulsing = False
        self.setFixedSize(size + 8, size + 8)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def set_pulsing(self, pulsing: bool) -> None:
        self._pulsing = pulsing
        if pulsing:
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase += 0.14
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._color)
        if self._pulsing:
            halo = QColor(color)
            halo.setAlpha(int(45 + 60 * (0.5 + 0.5 * math.sin(self._phase))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        offset = (self.width() - self._size) / 2.0
        painter.drawEllipse(QRectF(offset, offset, self._size, self._size))
        painter.end()


# --------------------------------------------------------------------------- #
# Текст с многоточием
# --------------------------------------------------------------------------- #
class ElidedLabel(QWidget):
    """Однострочный текст с многоточием на конце."""

    def __init__(self, text: str = "", color: QColor | None = None, parent=None):
        super().__init__(parent)
        self._text = text
        self._color = QColor(color) if color is not None else None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(14)
        self.setFont(font)
        self.setMinimumHeight(QFontMetrics(font).height() + 2)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        text = text or ""
        if text == self._text:
            return
        self._text = text
        self.update()

    def text(self) -> str:
        return self._text

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        color = self._color or self.palette().color(QPalette.ColorRole.WindowText)
        painter.setPen(QPen(color))
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        text = metrics.elidedText(
            self._text, Qt.TextElideMode.ElideRight, max(0, self.width() - 2)
        )
        painter.drawText(
            QRectF(0, 0, self.width(), self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.end()


# --------------------------------------------------------------------------- #
# Редактор горячих клавиш
# --------------------------------------------------------------------------- #
class HotkeyEdit(QLineEdit):
    """Поле, которое записывает нажатое сочетание клавиш.

    Пользователь кликает в поле и нажимает сочетание — оно сразу сохраняется.
    Esc или Backspace без модификаторов очищают значение.
    """

    changed = Signal(str)

    def __init__(self, spec: str = "", single_key: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("HotkeyField")
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPlaceholderText("Нажмите сочетание…")
        self.setMinimumWidth(180)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._single_key = single_key
        self._spec = ""
        self.set_spec(spec, emit=False)
        self.editingFinished.connect(self._restore_display)

    # --- API ---------------------------------------------------------------
    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str, emit: bool = True) -> None:
        self._spec = (spec or "").strip().lower()
        self._restore_display()
        if emit and self._spec:
            self.changed.emit(self._spec)

    def _restore_display(self) -> None:
        self.setText(formatting.format_hotkey(self._spec) if self._spec else "")

    # --- события -----------------------------------------------------------
    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().focusInEvent(event)
        self.setPlaceholderText("Нажмите сочетание…")
        self.selectAll()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self.setPlaceholderText("Нажмите сочетание…")
        self._restore_display()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        key = event.key()
        if key in {
            Qt.Key.Key_Shift,
            Qt.Key.Key_Control,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_AltGr,
            Qt.Key.Key_CapsLock,
            Qt.Key.Key_NumLock,
            Qt.Key.Key_ScrollLock,
        }:
            return
        modifiers = event.modifiers()
        no_modifier = modifiers == Qt.KeyboardModifier.NoModifier
        if no_modifier and key == Qt.Key.Key_Escape:
            self.set_spec("")
            self.clearFocus()
            return
        if no_modifier and key in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete}:
            self.set_spec("")
            return
        if modifiers & Qt.KeyboardModifier.KeypadModifier:
            modifiers &= ~Qt.KeyboardModifier.KeypadModifier
        modifier_bits = int(getattr(modifiers, "value", modifiers))
        key_bits = int(getattr(key, "value", key))
        combined = modifier_bits | key_bits
        sequence = QKeySequence(combined)
        spec = formatting.key_sequence_to_spec(sequence)
        if not spec:
            return
        parts = [part for part in spec.split("+") if part]
        if self._single_key and len(parts) > 1:
            # Клавиша отмены — одиночная: модификаторы не поддерживаем.
            return
        self.set_spec(spec)
        self._restore_display()


# --------------------------------------------------------------------------- #
# Диалоги подтверждения
# --------------------------------------------------------------------------- #
def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    ok_text: str = "Продолжить",
    cancel_text: str = "Отмена",
    danger: bool = False,
) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    ok_button = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    if danger:
        ok_button.setObjectName("Danger")
    box.exec()
    return box.clickedButton() is ok_button


def show_info(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def open_external(url: str) -> None:
    """Открывает ссылку в браузере по умолчанию."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))


def copy_text(text: str) -> None:
    """Копирует текст в буфер обмена приложения."""
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text or "")
