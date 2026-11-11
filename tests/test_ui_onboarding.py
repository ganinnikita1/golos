"""Тесты мастера первого запуска."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from golos.ui.demo import FakeController
from golos.ui.onboarding import TOTAL_STEPS, OnboardingWizard


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


def make_wizard(qapp) -> tuple[OnboardingWizard, FakeController]:
    controller = FakeController()
    controller.cfg.onboarding_done = False
    wizard = OnboardingWizard(controller)
    return wizard, controller


def test_wizard_steps_and_navigation(qapp):
    wizard, _ = make_wizard(qapp)
    assert wizard.stack.count() == TOTAL_STEPS == 4
    assert wizard.current_step() == 0
    assert wizard.back_button.isHidden()
    wizard.go_to_step(1)
    assert wizard.current_step() == 1
    assert not wizard.back_button.isHidden()
    assert wizard.step_label.text() == "Шаг 2 из 4"
    wizard.go_to_step(3)
    assert wizard.next_button.text() == "Начать пользоваться"
    assert wizard.skip_button.isHidden()
    wizard.close()


def test_finish_marks_onboarding_done(qapp):
    wizard, controller = make_wizard(qapp)
    captured: list = []
    wizard.completed.connect(captured.append)
    wizard.autostart_toggle.setChecked(True)
    wizard.overlay_toggle.setChecked(False)
    wizard.go_to_step(3)
    wizard._finish()
    assert controller.cfg.onboarding_done is True
    assert controller.cfg.ui.autostart is True
    assert controller.cfg.ui.overlay_enabled is False
    assert len(captured) == 1
    assert captured[0].onboarding_done is True
    assert wizard.result() != 0


def test_skip_marks_done_without_completed_signal(qapp):
    wizard, controller = make_wizard(qapp)
    captured: list = []
    wizard.completed.connect(captured.append)
    wizard._skip()
    assert controller.cfg.onboarding_done is True
    assert captured == []


def test_progress_updates_bar(qapp):
    wizard, _ = make_wizard(qapp)
    wizard.go_to_step(2)
    wizard.on_progress("Скачиваем модель…", 0.42)
    assert wizard.model_progress.isVisibleTo(wizard)
    assert wizard.model_progress.value() == 42
    wizard.on_progress("Готово", None)
    assert not wizard.model_progress.isVisibleTo(wizard)
    wizard.close()


def test_microphone_test_button(qapp):
    wizard, controller = make_wizard(qapp)
    controller.test_microphone = lambda: True  # type: ignore[method-assign]
    wizard._test_microphone()
    assert wizard.test_button.isEnabled() is False
    wizard.on_level(0.5)
    wizard._finish_test()
    assert wizard.test_button.isEnabled() is True
    wizard.close()


def test_model_status_when_loaded(qapp):
    wizard, controller = make_wizard(qapp)
    controller.engine_status = lambda: {  # type: ignore[method-assign]
        "engine": "gigaam",
        "loaded": True,
        "loading": False,
        "message": "Готово",
    }
    wizard._refresh_model_status()
    assert wizard.load_model_button.text() == "Модель готова"
    assert wizard.load_model_button.isEnabled() is False
    wizard.close()


def test_devices_loaded(qapp):
    wizard, _ = make_wizard(qapp)
    assert wizard.device_combo.count() >= 1
    assert wizard.device_combo.itemData(0) == ""
    wizard.close()
