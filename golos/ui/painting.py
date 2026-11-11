"""Векторные примитивы для кастомной отрисовки: микрофон, спиннер, «пилюля».

Иконки трея и приложения — PNG из ``golos/assets`` (их собирает
``golos.ui.assets_build``), а мелкая графика внутри оверлея рисуется здесь,
чтобы оставаться чёткой при любом масштабе экрана.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def paint_mic(
    painter: QPainter,
    rect: QRectF,
    color: QColor,
    width: float | None = None,
) -> None:
    """Рисует микрофон, вписанный в rect."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    stroke = width if width is not None else max(1.2, rect.width() * 0.075)
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    x = rect.left()
    y = rect.top()
    w = rect.width()
    h = rect.height()

    # Капсула.
    capsule_w = w * 0.30
    capsule_h = h * 0.42
    capsule = QRectF(
        x + (w - capsule_w) / 2.0,
        y + h * 0.10,
        capsule_w,
        capsule_h,
    )
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(capsule, capsule_w / 2.0, capsule_w / 2.0)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen)

    # Держатель: нижняя половина окружности.
    holder = QRectF(
        x + w * 0.22,
        y + h * 0.30,
        w * 0.56,
        h * 0.42,
    )
    path = QPainterPath()
    path.arcMoveTo(holder, 180.0)
    path.arcTo(holder, 180.0, 180.0)
    painter.drawPath(path)

    # Ножка и основание.
    stem_top = y + h * 0.72
    painter.drawLine(QPointF(x + w / 2.0, stem_top), QPointF(x + w / 2.0, y + h * 0.84))
    painter.drawLine(
        QPointF(x + w * 0.34, y + h * 0.87),
        QPointF(x + w * 0.66, y + h * 0.87),
    )
    painter.restore()


def paint_spinner(
    painter: QPainter,
    rect: QRectF,
    color: QColor,
    phase: float,
    width: float | None = None,
) -> None:
    """Вращающаяся дуга («распознаю…»)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    stroke = width if width is not None else max(1.6, rect.width() * 0.10)
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    start = (-phase * 360.0) % 360.0
    painter.drawArc(rect, int((90.0 - start) * 16), int(280.0 * 16))
    painter.restore()


def paint_exclamation(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Восклицательный знак для состояния ошибки."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    bar_w = rect.width() * 0.20
    bar = QRectF(
        rect.center().x() - bar_w / 2.0,
        rect.top() + rect.height() * 0.16,
        bar_w,
        rect.height() * 0.46,
    )
    painter.drawRoundedRect(bar, bar_w / 2.0, bar_w / 2.0)
    dot_r = rect.width() * 0.11
    painter.drawEllipse(
        QPointF(rect.center().x(), rect.top() + rect.height() * 0.80),
        dot_r,
        dot_r,
    )
    painter.restore()


def paint_wave_bars(
    painter: QPainter,
    rect: QRectF,
    color: QColor,
    values: list[float],
) -> None:
    """Мини-эквалайзер (используется в иконке «вставлено»)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    if not values:
        painter.restore()
        return
    count = len(values)
    gap = rect.width() * 0.06
    bar_w = (rect.width() - gap * (count - 1)) / count
    for index, value in enumerate(values):
        value = max(0.08, min(1.0, float(value)))
        height = rect.height() * value
        bar = QRectF(
            rect.left() + index * (bar_w + gap),
            rect.center().y() - height / 2.0,
            bar_w,
            height,
        )
        painter.setBrush(color)
        painter.drawRoundedRect(bar, bar_w / 2.0, bar_w / 2.0)
    painter.restore()


def pulse(value: float, phase: float, speed: float = 1.0) -> float:
    """0..1 синусоида для анимаций."""
    return 0.5 + 0.5 * math.sin(phase * speed + value * math.pi * 2.0)
