"""Загрузка иконок из ``golos/assets`` с запасной векторной отрисовкой.

Иконки трея — PNG, сгенерированные ``golos.ui.assets_build`` (несколько
размеров, чтобы Windows выбирала чёткий вариант под масштаб). Если файлов нет
(например, ресурсы ещё не собраны), иконка рисуется кодом — UI не падает.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from .. import paths
from . import painting

#: Размеры, в которых поставляются PNG трея.
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)

#: Состояние контроллера → вариант иконки трея.
STATE_TO_ICON = {
    "idle": "idle",
    "loading": "busy",
    "recording": "recording",
    "transcribing": "busy",
    "polishing": "busy",
    "inserting": "busy",
    "error": "error",
}

_ICON_COLORS = {
    "idle": ("#313844", "#232830", "#D3DCE6"),
    "recording": ("#3ED3C4", "#22A99D", "#06211E"),
    "busy": ("#5A9BF0", "#3B6FD4", "#F5F9FF"),
    "error": ("#EC6A66", "#C94743", "#FFF5F4"),
}


def icon_kind_for_state(state: str) -> str:
    return STATE_TO_ICON.get(state or "idle", "idle")


def _render_tray_fallback(kind: str, size: int) -> QPixmap:
    """Запасная иконка: тот же образ, но нарисованный Qt."""
    top, bottom, glyph = _ICON_COLORS.get(kind, _ICON_COLORS["idle"])
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bubble = QRectF(0.5, 0.5, size - 1, size - 1)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(top))
    painter.drawEllipse(bubble)
    glyph_rect = QRectF(size * 0.22, size * 0.18, size * 0.56, size * 0.64)
    color = QColor(glyph)
    if kind == "error":
        painting.paint_exclamation(painter, glyph_rect.adjusted(size * 0.08, size * 0.06, -size * 0.08, -size * 0.06), color)
    else:
        painting.paint_mic(painter, glyph_rect, color, width=max(1.0, size * 0.055))
    painter.end()
    return pixmap


def tray_icon(state: str) -> QIcon:
    """QIcon для состояния контроллера (idle/recording/transcribing/error…)."""
    kind = icon_kind_for_state(state)
    icon = QIcon()
    folder = paths.assets_dir() / "tray"
    for size in TRAY_SIZES:
        path = folder / f"{kind}_{size}.png"
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                icon.addPixmap(pixmap)
    flat = paths.assets_dir() / f"tray_{kind}.png"
    if flat.is_file():
        pixmap = QPixmap(str(flat))
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    if icon.isNull():
        icon = QIcon(_render_tray_fallback(kind, 32))
    return icon


def app_icon() -> QIcon:
    """Иконка приложения в нескольких размерах."""
    icon = QIcon()
    folder = paths.assets_dir() / "app"
    for size in (16, 24, 32, 48, 64, 128, 256):
        path = folder / f"golos_{size}.png"
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                icon.addPixmap(pixmap)
    if icon.isNull():
        icon = QIcon(_render_tray_fallback("recording", 64))
    return icon


def app_pixmap(size: int = 96) -> QPixmap:
    """Одна PNG-иконка приложения нужного размера (для страницы «О программе»)."""
    folder = paths.assets_dir() / "app"
    for candidate in (256, 128, 64, 48, 32, 16):
        path = folder / f"golos_{candidate}.png"
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return _render_tray_fallback("recording", size)


__all__ = [
    "app_icon",
    "app_pixmap",
    "icon_kind_for_state",
    "tray_icon",
]
