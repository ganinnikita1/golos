"""Иконка в трее и её меню.

Меню — основная точка управления приложением, когда окно спрятано:
диктовка, отмена, настройки, история, статус модели, автозапуск, выход.
Иконка меняется по состоянию (idle / recording / busy / error).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_TITLE
from . import formatting, icons

logger = logging.getLogger(__name__)


class GolosTray(QObject):
    """Трей-иконка «Голоса» с меню действий."""

    toggle_requested = Signal()
    cancel_requested = Signal()
    settings_requested = Signal()
    history_requested = Signal()
    autostart_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._state = "idle"
        self._cfg = None
        self._menu = QMenu()
        self._build_menu()

        self._tray = QSystemTrayIcon(icons.app_icon(), self)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.messageClicked.connect(self._on_message_clicked)
        self.refresh()

    # ------------------------------------------------------------------ API
    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def start(self) -> None:
        """Показать иконку в трее."""
        self._tray.show()

    def stop(self) -> None:
        self._tray.hide()

    def set_state(self, state: str) -> None:
        """Сменить иконку и подпись действия под состояние контроллера."""
        self._state = state or "idle"
        self._tray.setIcon(icons.tray_icon(self._state))
        self._update_dictation_action()
        if self._state == "recording":
            self._tray.setToolTip("Голос — идёт запись. Нажмите, чтобы остановить.")
        elif self._state in {"transcribing", "polishing", "inserting"}:
            self._tray.setToolTip("Голос — распознаю запись…")
        elif self._state == "error":
            self._tray.setToolTip("Голос — произошла ошибка.")
        else:
            self._tray.setToolTip(self._idle_tooltip())

    def refresh(self) -> None:
        """Перечитать конфигурацию и статус модели (после сохранения настроек)."""
        try:
            self._cfg = self._controller.get_config()
        except Exception as exc:  # ядро не должно ронять трей
            logger.warning("Не удалось прочитать конфигурацию для трея: %s", exc)
            self._cfg = None
        try:
            status = self._controller.engine_status() or {}
        except Exception as exc:
            logger.warning("Не удалось получить статус модели: %s", exc)
            status = {}
        self.update_model_status(status)
        self._update_autostart()
        self._update_dictation_action()
        self._tray.setToolTip(
            self._idle_tooltip() if self._state in {"idle", "error"} else self._tray.toolTip()
        )

    def update_model_status(self, status: dict) -> None:
        """Обновить строку «Модель: …» в меню (без перечитывания конфига)."""
        self._update_model_action(status or {})

    def show_message(
        self,
        message: str,
        title: str = "Голос",
        level: str = "info",
        timeout_ms: int = 4000,
    ) -> None:
        """Показать системное уведомление из трея."""
        icon = {
            "error": QSystemTrayIcon.MessageIcon.Critical,
            "warn": QSystemTrayIcon.MessageIcon.Warning,
            "warning": QSystemTrayIcon.MessageIcon.Warning,
        }.get(level, QSystemTrayIcon.MessageIcon.Information)
        self._tray.showMessage(title, message, icon, timeout_ms)

    # ------------------------------------------------------------ внутреннее
    def _build_menu(self) -> None:
        self.action_dictation = QAction("Начать диктовку", self._menu)
        self.action_dictation.triggered.connect(self.toggle_requested)
        self._menu.addAction(self.action_dictation)

        self.action_cancel = QAction("Отменить запись", self._menu)
        self.action_cancel.triggered.connect(self.cancel_requested)
        self.action_cancel.setVisible(False)
        self._menu.addAction(self.action_cancel)

        self._menu.addSeparator()

        self.action_settings = QAction("Настройки…", self._menu)
        self.action_settings.triggered.connect(self.settings_requested)
        self._menu.addAction(self.action_settings)

        self.action_history = QAction("История…", self._menu)
        self.action_history.triggered.connect(self.history_requested)
        self._menu.addAction(self.action_history)

        self._menu.addSeparator()

        self.action_model = QAction("Модель: —", self._menu)
        self.action_model.setEnabled(False)
        self._menu.addAction(self.action_model)

        self.action_autostart = QAction("Автозапуск при входе в Windows", self._menu)
        self.action_autostart.setCheckable(True)
        self.action_autostart.toggled.connect(self.autostart_toggled)
        self._menu.addAction(self.action_autostart)

        self._menu.addSeparator()

        self.action_quit = QAction("Выход", self._menu)
        self.action_quit.triggered.connect(self.quit_requested)
        self._menu.addAction(self.action_quit)

    def _idle_tooltip(self) -> str:
        hotkey = formatting.format_hotkey(
            self._cfg.hotkey.hotkey if self._cfg is not None else "ctrl+alt+space"
        )
        return f"{APP_TITLE}\nГорячая клавиша: {hotkey}\nЛКМ — начать диктовку"

    def _update_dictation_action(self) -> None:
        hotkey = ""
        if self._cfg is not None:
            hotkey = formatting.format_hotkey(self._cfg.hotkey.hotkey)
        hint = f"\t{hotkey}" if hotkey else ""
        busy = self._state in {"transcribing", "polishing", "inserting", "loading"}
        if self._state == "recording":
            self.action_dictation.setText(f"Остановить диктовку{hint}")
        else:
            self.action_dictation.setText(f"Начать диктовку{hint}")
        self.action_cancel.setVisible(busy or self._state == "recording")
        self.action_cancel.setEnabled(busy or self._state == "recording")

    def _update_model_action(self, status: dict) -> None:
        if self._cfg is None:
            self.action_model.setText("Модель: недоступна")
            return
        title = formatting.model_title(self._cfg)
        if status.get("loading"):
            suffix = "загружается…"
        elif status.get("loaded"):
            suffix = "готова"
        else:
            suffix = "не загружена"
        self.action_model.setText(f"Модель: {title} — {suffix}")

    def _update_autostart(self) -> None:
        if self._cfg is None:
            return
        was_blocked = self.action_autostart.blockSignals(True)
        self.action_autostart.setChecked(bool(self._cfg.ui.autostart))
        self.action_autostart.blockSignals(was_blocked)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.history_requested.emit()

    def _on_message_clicked(self) -> None:
        self.settings_requested.emit()


__all__ = ["GolosTray"]
