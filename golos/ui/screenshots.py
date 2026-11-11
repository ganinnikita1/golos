"""Генерация скриншотов интерфейса для docs/screenshots.

Запуск (рендерится без реального экрана)::

    python -m golos.ui.screenshots

Окна снимаются через QWidget.grab(), оверлей дополнительно накладывается на
нарисованный «рабочий стол», чтобы было видно, как «пилюля» выглядит поверх
другого приложения. Никаких внешних утилит и скриншотеров не используется.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from .. import __version__  # noqa: E402
from . import icons, theme  # noqa: E402
from .app_ui import create_application  # noqa: E402
from .overlay import OverlayWidget  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs" / "screenshots"


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #
def _settle(app: QApplication, milliseconds: int = 250) -> None:
    """Прокрутить цикл событий, чтобы применились стили, layout и анимации."""
    deadline = time.monotonic() + milliseconds / 1000.0
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


def _save(app: QApplication, widget: QWidget, name: str) -> Path:
    _settle(app, 220)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    pixmap = widget.grab()
    pixmap.save(str(path), "PNG")
    logger.info("Скриншот: %s (%dx%d)", path.relative_to(ROOT), pixmap.width(), pixmap.height())
    return path


def _demo_controller():
    from .demo import FakeController

    return FakeController()


# --------------------------------------------------------------------------- #
# «Рабочий стол» для скриншота оверлея
# --------------------------------------------------------------------------- #
class DesktopBackdrop(QWidget):
    """Нарисованный рабочий стол с открытым документом — фон для оверлея."""

    TITLE = "Заметки — Блокнот"
    BODY = (
        "Итоги встречи по проекту «Голос»",
        "",
        "1. Офлайн-распознавание на GigaAM-v3 работает на CPU, средняя задержка — "
        "около двух секунд на фразу.",
        "2. Настроили горячую клавишу: диктовка вызывается из любого приложения "
        "и не перехватывает фокус.",
        "3. Следующий шаг — собрать установщик и проверить работу на Windows 11 "
        "со 125 % масштабом.",
        "",
        "Проверил интеграцию с локальной моделью распознавания: всё работает "
        "офлайн, текст вставляется в активное окно.",
    )

    def __init__(self, width: int = 1440, height: int = 900, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        taskbar_h = 54

        # Фон рабочего стола.
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0.0, QColor("#1A2733"))
        gradient.setColorAt(0.55, QColor("#121A22"))
        gradient.setColorAt(1.0, QColor("#0C1116"))
        painter.fillRect(self.rect(), gradient)
        glow = QLinearGradient(0, 0, 700, 500)
        glow.setColorAt(0.0, QColor(79, 209, 197, 26))
        glow.setColorAt(1.0, QColor(79, 209, 197, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(QRectF(-120, -160, 900, 620), 240, 240)

        # Окно документа.
        window = QRectF(196, 128, 1048, 604)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(window.adjusted(6, 10, 6, 14), 14, 14)
        painter.setBrush(QColor("#1C222A"))
        painter.setPen(QPen(QColor("#2C3440"), 1))
        painter.drawRoundedRect(window, 12, 12)

        title_bar = QRectF(window.left(), window.top(), window.width(), 46)
        title_path = QRectF(window.left() + 0.5, window.top() + 0.5, window.width() - 1, 46)
        painter.setBrush(QColor("#242B34"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(title_path, 12, 12)
        painter.drawRect(QRectF(title_path.left(), title_path.bottom() - 12, title_path.width(), 12))
        painter.setPen(QPen(QColor("#2C3440"), 1))
        painter.drawLine(QPoint(int(window.left()), int(title_bar.bottom())), QPoint(int(window.right()), int(title_bar.bottom())))

        # Кнопки окна.
        for index in range(3):
            cx = window.right() - 30 - index * 34
            cy = title_bar.center().y()
            painter.setPen(Qt.PenStyle.NoPen)
            if index == 0:
                painter.setBrush(QColor("#E05B5B"))
            else:
                painter.setBrush(QColor("#39424E"))
            painter.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))

        # Заголовок и текст документа.
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#AEB9C6"))
        painter.drawText(
            QRectF(window.left() + 22, title_bar.top(), 400, title_bar.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.TITLE,
        )

        body_font = QFont(theme.FONT_FAMILY)
        body_font.setPixelSize(16)
        painter.setFont(body_font)
        metrics = QFontMetrics(body_font)
        text_rect = QRectF(window.left() + 46, window.top() + 84, window.width() - 92, window.height() - 120)
        y = text_rect.top()
        for line in self.BODY:
            painter.setPen(QColor("#E7EBF1") if line and not line.startswith(tuple("0123456789")) else QColor("#C8D2DE"))
            if not line:
                y += metrics.height() * 0.6
                continue
            painter.drawText(
                QRectF(text_rect.left(), y, text_rect.width(), metrics.height() * 1.6),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                line,
            )
            y += metrics.height() * 1.65

        # Каретка в конце документа.
        painter.setPen(QPen(QColor("#4FD1C5"), 2))
        painter.drawLine(QPoint(int(text_rect.left()), int(y - 4)), QPoint(int(text_rect.left()), int(y - 4 + metrics.height())))

        # Панель задач.
        taskbar = QRectF(0, height - taskbar_h, width, taskbar_h)
        painter.setBrush(QColor(15, 19, 24, 235))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(taskbar)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawLine(QPoint(0, height - taskbar_h), QPoint(width, height - taskbar_h))

        # Иконки запущенных приложений по центру.
        app_icon = icons.app_pixmap(30)
        icons_list = [(width / 2 - 132, app_icon), (width / 2 - 66, None), (width / 2, None), (width / 2 + 66, None)]
        for index, (x, pixmap) in enumerate(icons_list):
            box = QRectF(x, height - taskbar_h / 2 - 16, 32, 32)
            if pixmap is not None:
                painter.drawPixmap(box.toRect(), pixmap)
            else:
                painter.setBrush(QColor("#2A323D"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(box, 8, 8)
                painter.setBrush(QColor("#4C5866"))
                painter.drawRoundedRect(box.adjusted(7, 7, -7, -7), 4, 4)
            if index == 0:
                painter.setBrush(QColor("#4FD1C5"))
                painter.drawRoundedRect(QRectF(box.center().x() - 6, height - 5, 12, 3), 1.5, 1.5)

        # Область трея: наша иконка + системные.
        tray_icon = icons.tray_icon("recording").pixmap(20, 20)
        tray_x = width - 208
        painter.drawPixmap(QRectF(tray_x, height - taskbar_h / 2 - 10, 20, 20).toRect(), tray_icon)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8C96A3"))
        painter.drawEllipse(QRectF(tray_x + 44, height - taskbar_h / 2 - 1, 8, 8))
        painter.drawRoundedRect(QRectF(tray_x + 66, height - taskbar_h / 2 - 6, 12, 10), 2, 2)
        painter.drawRoundedRect(QRectF(tray_x + 86, height - taskbar_h / 2 - 4, 16, 8), 1.5, 1.5)

        clock_font = QFont(theme.FONT_FAMILY)
        clock_font.setPixelSize(12)
        painter.setFont(clock_font)
        painter.setPen(QColor("#D7DEE7"))
        painter.drawText(
            QRectF(width - 116, height - taskbar_h / 2 - 16, 104, 16),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            datetime.now().strftime("%H:%M"),
        )
        painter.setPen(QColor("#8C96A3"))
        painter.drawText(
            QRectF(width - 116, height - taskbar_h / 2 + 1, 104, 14),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            datetime.now().strftime("%d.%m.%Y"),
        )
        painter.end()


def _overlay_pixmap(app: QApplication, state: str, text: str) -> QPixmap:
    """Отрисовать оверлей в нужном состоянии на прозрачном фоне."""
    overlay = OverlayWidget()
    overlay.set_position("bottom_center")
    if state == "recording":
        overlay.start_recording()
        _settle(app, 260)
        for index in range(14):
            overlay.set_level(0.22 + 0.12 * (index % 3) + 0.28 * abs((index % 7) - 3) / 3.0)
            _settle(app, 45)
        overlay.set_text(text)
    elif state == "result":
        overlay.show_result(text)
    elif state == "error":
        overlay.show_error(text)
    _settle(app, 320)
    # Плавное появление может ещё идти — для скриншота фиксируем полную видимость.
    overlay._fade_effect.setOpacity(1.0)  # noqa: SLF001 - скриншот-утилита
    _settle(app, 80)
    pixmap = overlay.grab()
    overlay.hide()
    overlay.deleteLater()
    return pixmap


def _compose_overlay(app: QApplication, state: str, text: str, name: str) -> Path:
    backdrop = DesktopBackdrop()
    backdrop.show()
    _settle(app, 180)
    base = backdrop.grab()
    overlay_pixmap = _overlay_pixmap(app, state, text)
    dpr = base.devicePixelRatio() or 1.0
    result = QPixmap(base.size())
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, base)
    # Координаты — логические (у pixmap установлен devicePixelRatio).
    logical_width = base.width() / dpr
    logical_height = base.height() / dpr
    overlay_dpr = overlay_pixmap.devicePixelRatio() or 1.0
    overlay_width = overlay_pixmap.width() / overlay_dpr
    overlay_height = overlay_pixmap.height() / overlay_dpr
    x = (logical_width - overlay_width) / 2.0
    y = logical_height - overlay_height - 54.0 - 20.0
    painter.drawPixmap(QPointF(x, y), overlay_pixmap)
    painter.end()
    backdrop.hide()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    result.save(str(path), "PNG")
    logger.info("Скриншот: %s (%dx%d)", path.relative_to(ROOT), result.width(), result.height())
    return path


# --------------------------------------------------------------------------- #
# Окна приложения
# --------------------------------------------------------------------------- #
SETTINGS_PAGES = (
    ("main", 0),
    ("microphone", 1),
    ("recognition", 2),
    ("text", 3),
    ("polish", 4),
    ("about", 5),
)


def _settings_pages(
    app: QApplication,
    names: tuple[str, ...] = (),
    prefix: str = "settings",
) -> list[Path]:
    try:
        from .settings import SettingsWindow
    except Exception as exc:  # pragma: no cover - до готовности окна
        logger.warning("Окно настроек недоступно: %s", exc)
        return []
    controller = _demo_controller()
    window = SettingsWindow(controller)
    window.show()
    _settle(app, 320)
    outputs: list[Path] = []
    for slug, index in SETTINGS_PAGES:
        if names and slug not in names:
            continue
        window.set_page(index)
        _settle(app, 160)
        outputs.append(_save(app, window, f"{prefix}_{slug}.png"))
    window.close()
    window.deleteLater()
    return outputs


def _history_screenshot(app: QApplication) -> list[Path]:
    try:
        from .history import HistoryWindow
    except Exception as exc:  # pragma: no cover
        logger.warning("Окно истории недоступно: %s", exc)
        return []
    controller = _demo_controller()
    window = HistoryWindow(controller)
    window.show()
    _settle(app, 320)
    outputs = [_save(app, window, "history.png")]
    window.close()
    window.deleteLater()
    return outputs


def _onboarding_screenshots(app: QApplication) -> list[Path]:
    try:
        from .onboarding import OnboardingWizard
    except Exception as exc:  # pragma: no cover
        logger.warning("Мастер первого запуска недоступен: %s", exc)
        return []
    controller = _demo_controller()
    controller.cfg.onboarding_done = False
    wizard = OnboardingWizard(controller)
    wizard.show()
    _settle(app, 320)
    outputs = [_save(app, wizard, "onboarding_welcome.png")]
    wizard.go_to_step(1)
    _settle(app, 160)
    outputs.append(_save(app, wizard, "onboarding_microphone.png"))
    wizard.go_to_step(2)
    _settle(app, 160)
    outputs.append(_save(app, wizard, "onboarding_model.png"))
    wizard.close()
    wizard.deleteLater()
    return outputs


def _tray_menu_screenshot(app: QApplication) -> list[Path]:
    """Снимок контекстного меню трея (без показа на реальном экране)."""
    try:
        from .app_ui import GolosUI
        from .demo import FakeController
    except Exception as exc:  # pragma: no cover
        logger.warning("Меню трея недоступно: %s", exc)
        return []
    controller = FakeController()
    ui = GolosUI(controller)
    ui.start()
    _settle(app, 200)
    tray = getattr(ui, "_tray", None)
    menu = getattr(tray, "_menu", None)
    if menu is None:
        ui.shutdown()
        return []
    menu.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    menu.show()
    _settle(app, 160)
    outputs = [_save(app, menu, "tray_menu.png")]
    menu.hide()
    ui.shutdown()
    menu.deleteLater()
    return outputs


def _demo_panel_screenshot(app: QApplication) -> list[Path]:
    from .app_ui import GolosUI
    from .demo import DemoPanel, FakeController

    controller = FakeController()
    ui = GolosUI(controller)
    ui.start()
    panel = DemoPanel(controller, ui)
    panel.show()
    _settle(app, 320)
    outputs = [_save(app, panel, "demo_panel.png")]
    panel.hide()
    ui.shutdown()
    panel.deleteLater()
    return outputs


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_application([])
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app, "dark")
    logger.info("Версия %s, палитра %s", __version__, theme.current().name)

    _compose_overlay(
        app,
        "recording",
        "Проверим, как выглядит оверлей, пока идёт запись, а затем вставка готового текста.",
        "overlay_recording.png",
    )
    _compose_overlay(
        app,
        "result",
        "Проверил интеграцию с локальной моделью распознавания: всё работает офлайн, "
        "текст вставляется в активное окно.",
        "overlay_result.png",
    )
    _settings_pages(app)
    theme.apply(app, "light")
    _settings_pages(app, names=("main",), prefix="settings_light")
    theme.apply(app, "dark")
    _history_screenshot(app)
    _onboarding_screenshots(app)
    _demo_panel_screenshot(app)
    _tray_menu_screenshot(app)
    logger.info("Готово: %s", OUT_DIR.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
