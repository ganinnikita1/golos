"""Сборка интерфейса «Голоса» поверх контроллера ядра.

Публичный API (его использует golos.app):

    ui = GolosUI(controller)
    ui.start()
    ui.show_settings() / ui.show_history() / ui.show_onboarding()

``start()`` не запускает ``app.exec()`` и не открывает окон: ядро само владеет
циклом событий. QTimer на ``controller.bus.drain()`` живёт только здесь, в
главном потоке, поэтому Qt-объекты никогда не трогаются из фоновых потоков.
"""

from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, __version__, events
from . import icons, sounds, theme

logger = logging.getLogger(__name__)

#: Интервал опроса шины событий, мс (в диапазоне 25–40 мс из CONTRACTS.md).
DRAIN_INTERVAL_MS = 30
#: Как часто обновлять статус модели в трее, мс.
STATUS_INTERVAL_MS = 2000


class GolosUI(QObject):
    """Владелец трея, оверлея и окон приложения."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.bus = getattr(controller, "bus", None)
        self.state = "idle"
        self._app = QApplication.instance()
        self._cfg = self._read_config()
        self._tray = None
        self.overlay = None
        self._settings = None
        self._history = None
        self._onboarding = None
        self._started = False
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._drain)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_INTERVAL_MS)
        self._status_timer.timeout.connect(self._refresh_status)

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        """Создать трей и оверлей, начать читать шину событий."""
        if self._started:
            return
        self._started = True
        if self._app is not None:
            theme.apply(self._app, self._cfg.ui.theme)
            self._app.setWindowIcon(icons.app_icon())
        self._create_tray()
        self._create_overlay()
        if self.bus is not None:
            self._drain_timer.start()
        self._status_timer.start()
        self.state = "idle"
        if self._tray is not None:
            self._tray.set_state("idle")
            self._tray.start()
            if not self._tray.available and self._is_desktop():
                logger.warning("Системный трей недоступен — открываю настройки")
                QTimer.singleShot(0, self.show_settings)

    def show_settings(self) -> None:
        """Открыть окно настроек (или поднять уже открытое)."""
        from .settings import SettingsWindow

        if self._settings is not None:
            self._settings.refresh()
            self._settings.show()
            self._settings.raise_()
            self._settings.activateWindow()
            return
        window = SettingsWindow(self.controller, parent=None)
        window.saved.connect(self._on_config_saved)
        window.theme_preview_requested.connect(self._preview_theme)
        window.rejected.connect(self._on_settings_rejected)
        window.finished.connect(self._on_settings_closed)
        self._settings = window
        window.show()
        window.raise_()
        window.activateWindow()

    def show_history(self) -> None:
        """Открыть окно истории распознаваний."""
        from .history import HistoryWindow

        if self._history is not None:
            self._history.refresh()
            self._history.show()
            self._history.raise_()
            self._history.activateWindow()
            return
        window = HistoryWindow(self.controller, parent=None)
        window.finished.connect(self._on_history_closed)
        self._history = window
        window.show()
        window.raise_()
        window.activateWindow()

    def show_onboarding(self) -> None:
        """Открыть мастер первого запуска."""
        from .onboarding import OnboardingWizard

        if self._onboarding is not None:
            self._onboarding.show()
            self._onboarding.raise_()
            self._onboarding.activateWindow()
            return
        wizard = OnboardingWizard(self.controller, parent=None)
        wizard.completed.connect(self._on_onboarding_completed)
        wizard.finished.connect(self._on_onboarding_closed)
        self._onboarding = wizard
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()

    def shutdown(self) -> None:
        """Остановить таймеры и спрятать окна (вызывается при выходе)."""
        self._drain_timer.stop()
        self._status_timer.stop()
        if self.overlay is not None:
            self.overlay.dismiss()
            self.overlay.hide()
        for window in (self._settings, self._history, self._onboarding):
            if window is not None:
                window.hide()
        if self._tray is not None:
            self._tray.stop()

    def toggle_dictation(self) -> None:
        """Начать/остановить диктовку — действие трея и оверлея."""
        try:
            if self.state == "recording":
                self.controller.stop_recording()
            elif self.state in {"transcribing", "polishing", "inserting"}:
                self.controller.cancel()
            elif self.state in {"loading", "error"}:
                self.controller.start_recording()
            else:
                self.controller.start_recording()
        except Exception as exc:
            logger.exception("Не удалось переключить диктовку")
            self._notify_error(f"Не удалось запустить диктовку: {exc}")

    # ------------------------------------------------------------- обработчики
    def _drain(self) -> None:
        if self.bus is None:
            return
        try:
            batch = self.bus.drain(64)
        except Exception:  # pragma: no cover - шина не должна падать
            logger.exception("Ошибка чтения шины событий")
            return
        for event in batch:
            try:
                self._handle_event(event)
            except Exception:  # pragma: no cover - одно событие не должно ломать UI
                logger.exception("Ошибка обработки события %s", getattr(event, "type", "?"))

    def _handle_event(self, event) -> None:
        kind = getattr(event, "type", "")
        if kind == events.STATE:
            self._on_state(str(event.get("state", "idle")))
        elif kind == events.LEVEL:
            self._on_level(event.get("value", 0.0))
        elif kind == events.PARTIAL:
            self._on_partial(str(event.get("text", "")))
        elif kind == events.RESULT:
            self._on_result(event)
        elif kind == events.ERROR:
            self._on_error(event)
        elif kind == events.PROGRESS:
            self._on_progress(event)
        elif kind == events.TOAST:
            self._on_toast(event)

    def _on_state(self, state: str) -> None:
        previous = self.state
        self.state = state
        if self._tray is not None:
            self._tray.set_state(state)
        if state == "recording":
            if self._cfg.ui.overlay_enabled and self.overlay is not None:
                self.overlay.set_position(self._cfg.ui.overlay_position)
                self.overlay.start_recording()
            if self._cfg.ui.sounds:
                sounds.play("start")
        elif state in {"transcribing", "polishing", "inserting"}:
            if previous == "recording" and self._cfg.ui.sounds:
                sounds.play("stop")
            if self._cfg.ui.overlay_enabled and self.overlay is not None:
                self.overlay.set_state(state)
        elif state in {"idle", "error"}:
            if self.overlay is not None:
                self.overlay.set_state(state)

    def _on_level(self, value) -> None:
        try:
            number = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        if self.overlay is not None:
            self.overlay.set_level(number)
        if self._settings is not None and self._settings.isVisible():
            self._settings.on_level(number)

    def _on_partial(self, text: str) -> None:
        if self.overlay is not None:
            self.overlay.set_text(text)

    def _on_result(self, event) -> None:
        text = str(event.get("text", ""))
        inserted = bool(event.get("inserted", True))
        status = "Вставлено в активное окно" if inserted else "Текст распознан"
        if self.overlay is not None and self._cfg.ui.overlay_enabled:
            self.overlay.show_result(text, status)
        if self._history is not None and self._history.isVisible():
            self._history.refresh()
        if self._cfg.ui.show_history_after_insert and inserted:
            self.show_history()
        if not self._cfg.ui.overlay_enabled and self._tray is not None and text:
            # Не показываем сам текст в системном уведомлении: оно попадает в
            # журнал уведомлений Windows и на экран блокировки.
            action = "вставлен" if inserted else "распознан"
            self._tray.show_message(
                f"Текст {action} в активное окно ({len(text)} симв.)", "«Голос»"
            )

    def _on_error(self, event) -> None:
        message = str(event.get("message") or "Неизвестная ошибка")
        detail = event.get("detail")
        if detail:
            logger.debug("Детали ошибки: %s", detail)
        if self._cfg.ui.sounds:
            sounds.play("error")
        if self.overlay is not None and self._cfg.ui.overlay_enabled:
            self.overlay.show_error(message)
        if self._tray is not None:
            self._tray.show_message(message, level="error")
        if self._settings is not None and self._settings.isVisible():
            self._settings.set_status_message(message, role="danger")

    def _on_progress(self, event) -> None:
        message = str(event.get("message", ""))
        fraction = event.get("fraction")
        if self._onboarding is not None and self._onboarding.isVisible():
            self._onboarding.on_progress(message, fraction)
        if self._settings is not None and self._settings.isVisible():
            self._settings.on_progress(message, fraction)

    def _on_toast(self, event) -> None:
        message = str(event.get("message", ""))
        level = str(event.get("level", "info"))
        if self._tray is not None:
            self._tray.show_message(message, level=level)
        if self._settings is not None and self._settings.isVisible() and level in {"warn", "warning", "error"}:
            self._settings.set_status_message(message, role="danger" if level == "error" else "warning")

    # --- окна --------------------------------------------------------------
    def _on_config_saved(self, cfg) -> None:
        self._cfg = cfg
        if self._app is not None:
            theme.apply(self._app, cfg.ui.theme)
        if self.overlay is not None:
            self.overlay.set_position(cfg.ui.overlay_position)
            self.overlay.refresh_theme()
        if self._tray is not None:
            self._tray.refresh()

    def _preview_theme(self, name: str) -> None:
        if self._app is not None:
            theme.apply(self._app, name)
        if self.overlay is not None:
            self.overlay.refresh_theme()

    def _on_settings_rejected(self) -> None:
        if self._app is not None:
            theme.apply(self._app, self._cfg.ui.theme)
        if self.overlay is not None:
            self.overlay.refresh_theme()

    def _on_settings_closed(self) -> None:
        self._settings = None
        if self._cfg is not None and self._app is not None:
            theme.apply(self._app, self._cfg.ui.theme)

    def _on_history_closed(self) -> None:
        self._history = None

    def _on_onboarding_completed(self, cfg) -> None:
        self._on_config_saved(cfg)
        if self._tray is not None:
            self._tray.show_message(
                "Голос готов к работе. Нажмите горячую клавишу и говорите.",
                timeout_ms=6000,
            )

    def _on_onboarding_closed(self) -> None:
        self._onboarding = None

    # --- трей --------------------------------------------------------------
    def _create_tray(self) -> None:
        from .tray import GolosTray

        tray = GolosTray(self.controller, self)
        tray.toggle_requested.connect(self.toggle_dictation)
        tray.cancel_requested.connect(self._cancel)
        tray.settings_requested.connect(self.show_settings)
        tray.history_requested.connect(self.show_history)
        tray.autostart_toggled.connect(self._set_autostart)
        tray.quit_requested.connect(self._quit)
        self._tray = tray

    def _create_overlay(self) -> None:
        from .overlay import OverlayWidget

        overlay = OverlayWidget()
        overlay.set_position(self._cfg.ui.overlay_position)
        self.overlay = overlay

    def _cancel(self) -> None:
        try:
            self.controller.cancel()
        except Exception as exc:
            logger.exception("Не удалось отменить запись")
            self._notify_error(f"Не удалось отменить запись: {exc}")

    def _set_autostart(self, enabled: bool) -> None:
        cfg = copy.deepcopy(self._read_config())
        cfg.ui.autostart = bool(enabled)
        try:
            self.controller.apply_config(cfg, save=True)
            self._cfg = cfg
        except Exception as exc:
            logger.exception("Не удалось переключить автозапуск")
            self._notify_error(f"Не удалось изменить автозапуск: {exc}")
        finally:
            if self._tray is not None:
                self._tray.refresh()

    def _quit(self) -> None:
        try:
            self.controller.quit()
        except Exception:
            logger.exception("Ошибка при завершении контроллера")
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _refresh_status(self) -> None:
        if self._tray is None:
            return
        try:
            status = self.controller.engine_status() or {}
        except Exception:
            return
        self._tray.update_model_status(status)

    # --- вспомогательное ---------------------------------------------------
    def _read_config(self):
        try:
            return self.controller.get_config()
        except Exception:
            logger.exception("Не удалось прочитать конфигурацию, использую значения по умолчанию")
            from ..config import default_config

            return default_config()

    def _notify_error(self, message: str) -> None:
        if self._tray is not None:
            self._tray.show_message(message, level="error")
        if self.overlay is not None and self._cfg.ui.overlay_enabled:
            self.overlay.show_error(message)

    def _is_desktop(self) -> bool:
        try:
            return QGuiApplication.platformName() not in {"offscreen", "minimal", ""}
        except Exception:  # pragma: no cover
            return True


# --------------------------------------------------------------------------- #
# Запуск «только UI поверх контроллера» (отладка/демо); ядро использует
# GolosUI напрямую и само вызывает app.exec().
# --------------------------------------------------------------------------- #
def create_application(argv: list[str] | None = None) -> QApplication:
    """QApplication с метаданными и иконкой «Голоса» (идемпотентно)."""
    from PySide6.QtCore import Qt

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [APP_NAME])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Голос")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Golos")
    app.setWindowIcon(icons.app_icon())
    return app


def run(controller) -> int:
    """Поднять UI поверх готового контроллера и войти в цикл событий."""
    app = create_application()
    ui = GolosUI(controller)
    ui.start()
    if not controller.get_config().onboarding_done:
        QTimer.singleShot(0, ui.show_onboarding)
    return app.exec()


__all__ = [
    "DRAIN_INTERVAL_MS",
    "GolosUI",
    "create_application",
    "run",
]
