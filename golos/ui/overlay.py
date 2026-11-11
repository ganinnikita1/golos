"""Оверлей записи — «пилюля» по центру снизу.

Ключевые свойства:
* не забирает фокус (``WA_ShowWithoutActivating`` + ``WS_EX_NOACTIVATE``);
* не мешает кликам (``Qt.WindowTransparentForInput`` + ``WS_EX_TRANSPARENT``);
* не появляется в панели задач (``Qt.Tool`` + ``WS_EX_TOOLWINDOW``);
* плавно появляется и исчезает;
* показывает живые бары уровня, таймер, статус и распознанный текст.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import formatting, painting, theme
from .widgets import ElidedLabel, LevelMeter, StatusDot

logger = logging.getLogger(__name__)

#: Отступ под мягкую тень вокруг пилюли.
SHADOW_MARGIN = 26
PILL_WIDTH = 600
PILL_HEIGHT = 104
PILL_RADIUS = 24

#: Через сколько прятать оверлей после результата/ошибки, мс.
RESULT_HIDE_MS = 2600
ERROR_HIDE_MS = 4500

#: Тексты статусов по состоянию.
STATUS_TEXTS = {
    "recording": "Слушаю…",
    "transcribing": "Распознаю запись…",
    "polishing": "Улучшаю текст…",
    "inserting": "Вставляю в активное окно…",
}

#: Допустимые позиции из UIConfig.overlay_position.
POSITIONS = ("bottom_center", "top_center", "bottom_right", "near_cursor")


class _MicBadge(QWidget):
    """Круглый значок слева: микрофон, спиннер, галочка или «!»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self._mode = "mic"
        self._color_role = "accent"
        self._phase = 0.0

    def set_mode(self, mode: str, color_role: str = "accent") -> None:
        self._mode = mode
        self._color_role = color_role
        self.update()

    def set_phase(self, phase: float) -> None:
        self._phase = phase
        self.update()

    def _color(self, palette) -> QColor:
        if self._color_role == "danger":
            return theme.qcolor(palette.danger)
        if self._color_role == "success":
            return theme.qcolor(palette.success)
        if self._color_role == "muted":
            return theme.qcolor(palette.overlay_muted)
        return theme.qcolor(palette.accent)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = theme.current()
        color = self._color(palette)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Мягкая подложка.
        background = QColor(color)
        background.setAlpha(30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 13, 13)

        glyph = QRectF(self.rect()).adjusted(11, 10, -11, -10)
        if self._mode == "spinner":
            painting.paint_spinner(painter, glyph.adjusted(2, 2, -2, -2), color, self._phase, width=2.6)
        elif self._mode == "error":
            painting.paint_exclamation(painter, glyph, color)
        elif self._mode == "check":
            pen = QPen(color, 2.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(glyph.left() + glyph.width() * 0.12, glyph.center().y())
            path.lineTo(glyph.left() + glyph.width() * 0.40, glyph.top() + glyph.height() * 0.74)
            path.lineTo(glyph.left() + glyph.width() * 0.90, glyph.top() + glyph.height() * 0.26)
            painter.drawPath(path)
        else:
            painting.paint_mic(painter, glyph, color, width=2.4)

        # Пульс при записи.
        if self._mode == "mic" and self._color_role == "accent":
            ring = QColor(color)
            ring.setAlpha(int(30 + 45 * painting.pulse(0.0, self._phase, 1.6)))
            painter.setPen(QPen(ring, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 11, 11)
        painter.end()


class _FadeHost(QWidget):
    """Контейнер пилюли: рисует мягкую тень, а его прозрачность анимируется.

    Тень рисуется слоями прямо здесь, а не через QGraphicsDropShadowEffect:
    вложенные эффекты не дружат с полупрозрачным окном и ломают QWidget.grab().
    """

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pill = QRectF(
            SHADOW_MARGIN,
            SHADOW_MARGIN,
            PILL_WIDTH,
            PILL_HEIGHT,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        layers = 16
        for index in range(layers, 0, -1):
            fraction = index / layers
            spread = (SHADOW_MARGIN - 2) * fraction
            alpha = int(9 * (1.0 - fraction * 0.55))
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                pill.adjusted(-spread, -spread + 6, spread, spread + 8),
                PILL_RADIUS + spread,
                PILL_RADIUS + spread,
            )
        # Плотное ядро тени под нижней кромкой.
        for index in range(4, 0, -1):
            spread = 2.0 * index
            painter.setBrush(QColor(0, 0, 0, 12))
            painter.drawRoundedRect(
                pill.adjusted(-spread, -spread + 4, spread, spread + 6),
                PILL_RADIUS + spread,
                PILL_RADIUS + spread,
            )
        painter.end()


class _Pill(QWidget):
    """Фон пилюли: вертикальный градиент, рамка и верхний блик."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        palette = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0.0, theme.qcolor(palette.overlay_top))
        gradient.setColorAt(1.0, theme.qcolor(palette.overlay_bottom))
        painter.setBrush(gradient)
        painter.setPen(QPen(theme.qcolor(palette.overlay_border), 1.0))
        painter.drawRoundedRect(rect, PILL_RADIUS, PILL_RADIUS)

        # Верхний блик — очень мягкий, чтобы пилюля не выглядела плоской.
        highlight = QLinearGradient(0, 0, 0, rect.height() * 0.5)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 22))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -rect.height() * 0.35), PILL_RADIUS - 1, PILL_RADIUS - 1)
        painter.end()


class OverlayWidget(QWidget):
    """Всплывающая пилюля со статусом диктовки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAccessibleName("Оверлей записи «Голос»")
        self.setFixedSize(
            PILL_WIDTH + SHADOW_MARGIN * 2,
            PILL_HEIGHT + SHADOW_MARGIN * 2,
        )

        self._mode = "idle"
        self._position = "bottom_center"
        self._phase = 0.0
        self._elapsed = QElapsedTimer()
        self._elapsed_started = False
        self._fading_out = False
        self._native_applied = False

        # --- иерархия: окно -> fade_host -> пилюля -------------------------
        fade_host = _FadeHost(self)
        fade_host.setObjectName("OverlayFadeHost")
        fade_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(fade_host)
        self._fade_host = fade_host
        host_layout = QVBoxLayout(fade_host)
        host_layout.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        host_layout.setSpacing(0)

        self._fade_effect = QGraphicsOpacityEffect(fade_host)
        self._fade_effect.setOpacity(0.0)
        fade_host.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(170)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.finished.connect(self._on_fade_finished)

        pill = _Pill(fade_host)
        pill.setObjectName("OverlayPill")
        pill.setFixedSize(PILL_WIDTH, PILL_HEIGHT)
        self._pill = pill
        host_layout.addWidget(pill)

        content = QHBoxLayout(pill)
        content.setContentsMargins(20, 15, 20, 15)
        content.setSpacing(14)

        self.badge = _MicBadge(pill)
        content.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(2)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        self.status_dot = StatusDot(8, pill)
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = QLabel("", pill)
        self.status_label.setContentsMargins(2, 0, 0, 0)
        status_row.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        middle.addLayout(status_row)
        self.preview = ElidedLabel("", None, pill)
        middle.addWidget(self.preview)
        content.addLayout(middle, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        self.timer_label = QLabel("0:00", pill)
        self.timer_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.timer_label.setMinimumWidth(64)
        right.addWidget(self.timer_label)
        self.meter = LevelMeter(bars=10, bar_width=4, gap=3, height=24, parent=pill)
        self.meter.setFixedWidth(10 * 4 + 9 * 3)
        right.addWidget(self.meter, 0, Qt.AlignmentFlag.AlignRight)
        content.addLayout(right)

        # --- таймеры -------------------------------------------------------
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(100)
        self._display_timer.timeout.connect(self._update_clock)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(40)
        self._anim_timer.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)

        self._apply_palette(theme.current())
        self._show_preview_placeholder()

    # ------------------------------------------------------------------ API
    @property
    def mode(self) -> str:
        return self._mode

    def is_shown(self) -> bool:
        return self.isVisible() and not self._fading_out

    def opacity(self) -> float:
        return float(self._fade_effect.opacity())

    def set_position(self, position: str) -> None:
        self._position = position if position in POSITIONS else "bottom_center"

    def position(self) -> str:
        return self._position

    def start_recording(self) -> None:
        """Показать оверлей для только что начатой записи."""
        self._hide_timer.stop()
        self._mode = "recording"
        self._elapsed.restart()
        self._elapsed_started = True
        self._set_status("recording")
        self.badge.set_mode("mic", "accent")
        self.meter.set_role("accent")
        self.meter.set_mode("level")
        self.preview.set_color(theme.qcolor(theme.current().overlay_text))
        self._show_preview_placeholder()
        self.timer_label.setText("0:00")
        self._display_timer.start()
        self._anim_timer.start()
        self.present()

    def set_state(self, state: str) -> None:
        """Обновить оформление под состояние контроллера."""
        if state == "recording":
            if self._mode != "recording":
                self.start_recording()
            return
        if state in {"transcribing", "polishing", "inserting"}:
            self._hide_timer.stop()
            self._mode = "busy"
            self._set_status(state)
            self.badge.set_mode("spinner", "accent")
            self.badge.set_phase(self._phase)
            self.meter.set_mode("pulse")
            self.meter.set_role("accent")
            self._anim_timer.start()
            if not self.is_shown():
                self.present()
            return
        if state in {"idle", "error"}:
            # Для результата/ошибки свои таймеры — не прячем раньше времени.
            if self._mode in {"recording", "busy"}:
                if self._elapsed_started:
                    self._update_clock()
                self._schedule_hide(320)

    def set_level(self, value: float) -> None:
        if self._mode in {"recording", "busy"}:
            self.meter.set_level(value)

    def set_text(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.preview.set_color(theme.qcolor(theme.current().overlay_text))
            self.preview.setText(text)
        elif self._mode == "recording":
            self._show_preview_placeholder()

    def show_result(self, text: str, status: str = "Вставлено в активное окно") -> None:
        """Показать финальный текст, затем плавно спрятать."""
        self._display_timer.stop()
        self._anim_timer.stop()
        self._mode = "result"
        self.badge.set_mode("check", "success")
        self.meter.set_mode("idle")
        self._set_custom_status(status, "success", pulsing=False)
        text = (text or "").strip()
        if text:
            self.preview.set_color(theme.qcolor(theme.current().overlay_text))
            self.preview.setText(text)
        else:
            self._show_preview_placeholder("Текст не распознан")
        self.timer_label.setVisible(False)
        if not self.is_shown():
            self.present()
        self._schedule_hide(RESULT_HIDE_MS)

    def show_error(self, message: str) -> None:
        """Показать ошибку пользователю."""
        self._display_timer.stop()
        self._anim_timer.stop()
        self._mode = "error"
        self.badge.set_mode("error", "danger")
        self.meter.set_mode("idle")
        self.meter.set_role("danger")
        self._set_custom_status("Не получилось", "danger", pulsing=False)
        self.preview.set_color(theme.qcolor(theme.current().danger))
        self.preview.setText(message or "Неизвестная ошибка")
        self.timer_label.setVisible(False)
        self.present()
        self._schedule_hide(ERROR_HIDE_MS)

    def dismiss(self) -> None:
        """Плавно спрятать оверлей."""
        self._hide_timer.stop()
        self._display_timer.stop()
        self._anim_timer.stop()
        if not self.isVisible():
            self._reset()
            return
        self._fading_out = True
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def present(self) -> None:
        """Показать оверлей в позиции из настроек, не забирая фокус."""
        self._hide_timer.stop()
        self._place_on_screen()
        self._fading_out = False
        if not self.isVisible():
            self.show()
        self._apply_native_flags()
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade_effect.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def refresh_theme(self) -> None:
        """Перекрасить оверлей после смены темы."""
        self._apply_palette(theme.current())
        self.badge.update()
        self._pill.update()
        self.preview.update()

    # ------------------------------------------------------------ внутреннее
    def _apply_palette(self, palette) -> None:
        mono = QFont(theme.MONO_FAMILY.split(",")[0].strip())
        mono.setPixelSize(19)
        mono.setWeight(QFont.Weight.DemiBold)
        self.timer_label.setFont(mono)
        self.timer_label.setStyleSheet(f"color: {palette.overlay_text}; background: transparent;")

        status_font = QFont(theme.FONT_FAMILY)
        status_font.setPixelSize(12)
        status_font.setWeight(QFont.Weight.DemiBold)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet("background: transparent;")
        self.preview.set_color(theme.qcolor(palette.overlay_muted))

    def _set_status(self, state: str) -> None:
        palette = theme.current()
        text = STATUS_TEXTS.get(state, "")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {palette.accent}; background: transparent;"
        )
        self.status_dot.set_color(theme.qcolor(palette.accent))
        self.status_dot.set_pulsing(state == "recording")
        self.timer_label.setVisible(True)
        self.meter.setVisible(True)

    def _set_custom_status(self, text: str, role: str, pulsing: bool = False) -> None:
        palette = theme.current()
        color = {
            "danger": palette.danger,
            "success": palette.success,
            "accent": palette.accent,
        }.get(role, palette.overlay_muted)
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.status_dot.set_color(theme.qcolor(color))
        self.status_dot.set_pulsing(pulsing)
        self.timer_label.setVisible(self._mode in {"recording", "busy"})
        self.meter.setVisible(self._mode in {"recording", "busy"})

    def _show_preview_placeholder(self, text: str = "Говорите — текст появится здесь") -> None:
        self.preview.set_color(theme.qcolor(theme.current().overlay_muted))
        self.preview.setText(text)

    def _schedule_hide(self, delay_ms: int) -> None:
        self._hide_timer.stop()
        self._hide_timer.start(delay_ms)

    def _update_clock(self) -> None:
        if not self._elapsed_started:
            return
        self.timer_label.setText(formatting.format_elapsed(self._elapsed.elapsed() / 1000.0))

    def _tick(self) -> None:
        self._phase += 0.07
        self.badge.set_phase(self._phase)

    def _on_fade_finished(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self._reset()

    def _reset(self) -> None:
        self._mode = "idle"
        self._elapsed_started = False
        self._fade_effect.setOpacity(0.0)
        self.preview.setText("")
        self.status_label.setText("")
        self.timer_label.setText("0:00")
        self.timer_label.setVisible(True)
        self.meter.set_mode("idle")
        self.meter.set_role("accent")
        self.meter.setVisible(True)
        self.badge.set_mode("mic", "accent")
        self.status_dot.set_pulsing(False)

    # --- геометрия ---------------------------------------------------------
    def _screen(self):
        screen = None
        try:
            if self._position == "near_cursor":
                screen = QGuiApplication.screenAt(QCursor.pos())
        except Exception:  # pragma: no cover - платформенные особенности
            screen = None
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen

    def _place_on_screen(self) -> None:
        screen = self._screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        pill_w, pill_h = PILL_WIDTH, PILL_HEIGHT
        margin = 64
        position = self._position
        if position == "top_center":
            pill_x = available.left() + (available.width() - pill_w) // 2
            pill_y = available.top() + 72
        elif position == "bottom_right":
            pill_x = available.right() - pill_w - margin // 2
            pill_y = available.bottom() - pill_h - margin
        elif position == "near_cursor":
            cursor = QCursor.pos()
            pill_x = cursor.x() - pill_w // 2
            pill_y = cursor.y() + 28
            if pill_y + pill_h > available.bottom() - 12:
                pill_y = cursor.y() - pill_h - 28
        else:  # bottom_center
            pill_x = available.left() + (available.width() - pill_w) // 2
            pill_y = available.bottom() - pill_h - margin

        window_w = self.width()
        window_h = self.height()
        window_x = pill_x - SHADOW_MARGIN
        window_y = pill_y - SHADOW_MARGIN
        window_x = max(available.left(), min(window_x, available.right() - window_w + 1))
        window_y = max(available.top(), min(window_y, available.bottom() - window_h + 1))
        self.move(QPoint(window_x, window_y))

    # --- Win32 -------------------------------------------------------------
    def _apply_native_flags(self) -> None:
        """WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW поверх Qt."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_noactivate = 0x08000000
            ws_ex_transparent = 0x00000020
            ws_ex_toolwindow = 0x00000080
            ws_ex_layered = 0x00080000
            style = int(user32.GetWindowLongW(hwnd, gwl_exstyle)) & 0xFFFFFFFF
            new_style = style | ws_ex_noactivate | ws_ex_transparent | ws_ex_toolwindow | ws_ex_layered
            if new_style != style:
                if user32.SetWindowLongW(hwnd, gwl_exstyle, new_style) == 0:
                    logger.debug("SetWindowLongW вернул 0 для оверлея (HWND=%s)", hwnd)
            hwnd_topmost = -1
            swp_nomove = 0x0002
            swp_nosize = 0x0001
            swp_noactivate = 0x0010
            swp_showwindow = 0x0040
            user32.SetWindowPos(
                hwnd,
                hwnd_topmost,
                0,
                0,
                0,
                0,
                swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
            )
            self._native_applied = True
        except Exception as exc:  # pragma: no cover - только Windows
            logger.warning("Не удалось применить Win32-стили оверлея: %s", exc)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        # HWND появляется только после show() — применяем стили ещё раз.
        QTimer.singleShot(0, self._apply_native_flags)
