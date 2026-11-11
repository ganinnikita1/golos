"""Тесты сборки UI: трей, маршрутизация событий, окна, сохранение настроек."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from golos.config import default_config
from golos.controller import DeviceInfo
from golos.events import EventBus
from golos.ui import theme
from golos.ui.app_ui import DRAIN_INTERVAL_MS, GolosUI


class FakeBusController:
    """Минимальный контроллер для проверки UI без таймеров и модели."""

    def __init__(self):
        self.bus = EventBus()
        self.cfg = default_config()
        self.cfg.onboarding_done = True
        self.applied: list = []
        self.started = 0
        self.stopped = 0
        self.cancelled = 0
        self.history_items: list = []
        self.quit_called = False
        self.engine_loaded = False
        self.engine_loading = False
        self.clipboard_text = ""

    def get_config(self):
        return self.cfg

    def apply_config(self, cfg, *, save: bool = True) -> None:
        self.cfg = cfg
        self.applied.append(cfg)

    def get_devices(self):
        return [DeviceInfo(index=-1, name="Микрофон по умолчанию", is_default=True)]

    def start_recording(self) -> None:
        self.started += 1

    def stop_recording(self) -> None:
        self.stopped += 1

    def cancel(self) -> None:
        self.cancelled += 1

    def is_busy(self) -> bool:
        return False

    def load_engine(self) -> None:
        self.engine_loaded = True

    def engine_status(self) -> dict:
        return {
            "engine": self.cfg.asr.engine,
            "loaded": self.engine_loaded,
            "loading": self.engine_loading,
            "message": "",
        }

    def test_microphone(self) -> bool:
        return True

    def history(self):
        return list(self.history_items)

    def clear_history(self) -> None:
        self.history_items.clear()

    def insert_text(self, text: str) -> bool:
        return True

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_text = text

    def open_config_dir(self) -> None:
        return None

    def quit(self) -> None:
        self.quit_called = True


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def wait(app, milliseconds: int = 120) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    app.processEvents()


@pytest.fixture()
def ui(qapp):
    controller = FakeBusController()
    widget = GolosUI(controller)
    widget.start()
    yield widget, controller
    widget.shutdown()


def test_start_creates_tray_overlay_and_drain(qapp, ui):
    widget, _controller = ui
    assert widget.overlay is not None
    assert widget._tray is not None
    assert widget._drain_timer.isActive()
    assert 25 <= DRAIN_INTERVAL_MS <= 40
    assert widget.overlay.position() == "bottom_center"


def test_state_events_route_to_overlay_and_tray(qapp, ui):
    widget, controller = ui
    controller.bus.emit("state", state="recording")
    wait(qapp)
    assert widget.state == "recording"
    assert widget.overlay.mode == "recording"
    assert widget.overlay.isVisible()
    assert "Остановить" in widget._tray.action_dictation.text()

    controller.bus.emit("level", value=0.5)
    controller.bus.emit("partial", text="Промежуточный текст")
    wait(qapp)
    assert widget.overlay.preview.text() == "Промежуточный текст"

    controller.bus.emit("state", state="transcribing")
    wait(qapp)
    assert widget.overlay.mode == "busy"

    controller.bus.emit("state", state="idle")
    wait(qapp, 700)
    assert widget.state == "idle"
    assert "Начать" in widget._tray.action_dictation.text()


def test_result_and_error_events(qapp, ui):
    widget, controller = ui
    controller.bus.emit("state", state="recording")
    wait(qapp)
    controller.bus.emit(
        "result",
        text="Готовый текст",
        raw_text="Готовый текст",
        elapsed_s=2.0,
        rtf=0.1,
        engine="gigaam",
        inserted=True,
    )
    wait(qapp)
    assert widget.overlay.mode == "result"
    assert widget.overlay.preview.text() == "Готовый текст"

    controller.bus.emit("error", message="Тестовая ошибка", detail="trace")
    wait(qapp)
    assert widget.overlay.mode == "error"
    assert "Тестовая" in widget.overlay.preview.text()


def test_toggle_dictation_calls_controller(qapp, ui):
    widget, controller = ui
    controller.bus.emit("state", state="idle")
    wait(qapp)
    widget.toggle_dictation()
    assert controller.started == 1
    controller.bus.emit("state", state="recording")
    wait(qapp)
    widget.toggle_dictation()
    assert controller.stopped == 1
    controller.bus.emit("state", state="transcribing")
    wait(qapp)
    widget.toggle_dictation()
    assert controller.cancelled == 1


def test_autostart_toggle_applies_config(qapp, ui):
    widget, controller = ui
    widget._set_autostart(True)
    assert controller.applied
    assert controller.applied[-1].ui.autostart is True


def test_settings_roundtrip(qapp, ui):
    widget, controller = ui
    widget.show_settings()
    wait(qapp, 60)
    assert widget._settings is not None
    settings = widget._settings
    settings.punctuation_toggle.setChecked(False)
    settings.theme_combo.setCurrentIndex(settings.theme_combo.findData("light"))
    wait(qapp)
    assert theme.current().name == "light"
    settings._save()
    wait(qapp, 60)
    assert controller.applied
    assert controller.applied[-1].text.punctuation is False
    assert controller.applied[-1].ui.theme == "light"
    assert widget._cfg.ui.theme == "light"
    theme.apply(qapp, "dark")


def test_history_and_onboarding_windows(qapp, ui):
    widget, _controller = ui
    widget.show_history()
    wait(qapp, 60)
    assert widget._history is not None
    widget._history.close()
    wait(qapp, 60)
    assert widget._history is None

    widget.show_onboarding()
    wait(qapp, 60)
    assert widget._onboarding is not None
    widget._onboarding.close()
    wait(qapp, 60)
    assert widget._onboarding is None


def test_progress_and_toast_are_safe(qapp, ui):
    widget, controller = ui
    controller.bus.emit("progress", message="Скачиваем", fraction=0.3)
    controller.bus.emit("toast", message="Готово", level="info")
    controller.engine_loading = True
    wait(qapp, 120)
    assert widget.state == "idle"
