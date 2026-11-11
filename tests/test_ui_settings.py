"""Тесты окна настроек: все разделы, чтение/запись AppConfig, валидация."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from golos.ui.demo import FakeController
from golos.ui.settings import NAV_ITEMS, SettingsWindow


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


def make_window(qapp) -> tuple[SettingsWindow, FakeController]:
    controller = FakeController()
    window = SettingsWindow(controller)
    return window, controller


def test_all_pages_present(qapp):
    window, _ = make_window(qapp)
    assert window.stack.count() == len(NAV_ITEMS) == 6
    assert window.nav.count() == 6
    for index in range(6):
        window.set_page(index)
        assert window.current_page() == index
    assert not window.isVisible()
    window.close()


def test_values_loaded_from_config(qapp):
    window, _ = make_window(qapp)
    assert window.hotkey_edit.text() == "Ctrl+Alt+Space"
    assert window.cancel_edit.text() == "Esc"
    assert window.mode_combo.currentData() == "toggle"
    assert window.autostart_toggle.isChecked() is False
    assert window.sounds_toggle.isChecked() is True
    assert window.engine_combo.currentData() == "gigaam"
    assert window.gigaam_variant_combo.currentData() == "e2e_rnnt"
    assert window.punctuation_toggle.isChecked() is True
    assert window.device_combo.count() >= 1
    assert window.device_combo.itemData(0) == ""
    window.close()


def test_engine_switch_toggles_cards(qapp):
    window, _ = make_window(qapp)
    window.set_page(2)
    window.engine_combo.setCurrentIndex(window.engine_combo.findData("whisper"))
    assert window.whisper_card.isVisibleTo(window)
    assert not window.gigaam_card.isVisibleTo(window)
    assert not window.cloud_card.isVisibleTo(window)
    window.engine_combo.setCurrentIndex(window.engine_combo.findData("cloud"))
    assert window.cloud_card.isVisibleTo(window)
    window.close()


def test_theme_preview_signal(qapp):
    window, _ = make_window(qapp)
    captured: list[str] = []
    window.theme_preview_requested.connect(captured.append)
    light_index = window.theme_combo.findData("light")
    window.theme_combo.setCurrentIndex(light_index)
    wait(qapp)
    assert captured == ["light"]
    window.close()


def test_save_applies_config(qapp):
    window, controller = make_window(qapp)
    applied: list = []
    original = controller.apply_config

    def apply(cfg, *, save=True):
        applied.append(cfg)
        original(cfg, save=save)

    controller.apply_config = apply  # type: ignore[method-assign]
    saved: list = []
    window.saved.connect(saved.append)

    window.punctuation_toggle.setChecked(False)
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("push_to_talk"))
    window.hotkey_edit.set_spec("ctrl+shift+d")
    window.overlay_toggle.setChecked(False)
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
    window._save()

    assert window.result() == QDialog.DialogCode.Accepted
    assert len(applied) == 1
    cfg = applied[0]
    assert cfg.text.punctuation is False
    assert cfg.hotkey.mode == "push_to_talk"
    assert cfg.hotkey.hotkey == "ctrl+shift+d"
    assert cfg.ui.overlay_enabled is False
    assert cfg.ui.theme == "light"
    assert len(saved) == 1
    assert controller.cfg.text.punctuation is False


def test_validation_blocks_bad_hotkey(qapp):
    window, controller = make_window(qapp)
    calls: list = []
    controller.apply_config = lambda cfg, save=True: calls.append(cfg)  # type: ignore[method-assign]
    window.hotkey_edit.set_spec("")
    window._save()
    assert calls == []
    assert window.result() != QDialog.DialogCode.Accepted
    assert window.status_line.text()
    window.close()


def test_cancel_key_conflict_detected(qapp):
    window, _ = make_window(qapp)
    window.cancel_edit.set_spec("ctrl+alt+space")
    window._save()
    assert "совпадает" in window.status_line.text().lower() or window.result() != QDialog.DialogCode.Accepted
    window.close()


def test_dictionary_rows(qapp):
    window, _ = make_window(qapp)
    window._add_dictionary_row()
    row = window.dictionary_table.rowCount() - 1
    window.dictionary_table.item(row, 0).setText("гига ам")
    window.dictionary_table.item(row, 1).setText("GigaAM")
    entries = window._collect_dictionary()
    assert entries, "словарь должен содержать добавленную запись"
    assert entries[-1].source == "гига ам"
    assert entries[-1].target == "GigaAM"
    window._save()
    assert any(entry.source == "гига ам" for entry in window._cfg.text.dictionary)
    window.close()


def test_microphone_test_and_progress(qapp):
    window, controller = make_window(qapp)
    window.set_page(2)
    controller.test_microphone = lambda: True  # type: ignore[method-assign]
    window._test_microphone()
    assert window.test_button.isEnabled() is False
    window.on_level(0.6)
    window.on_progress("Скачиваем модель…", 0.5)
    assert window.asr_progress.isVisibleTo(window)
    assert window.asr_progress.value() == 50
    window.on_progress("Готово", None)
    assert not window.asr_progress.isVisibleTo(window)
    window._finish_microphone_test()
    assert window.test_button.isEnabled() is True
    window.close()


def test_refresh_reads_new_config(qapp):
    window, controller = make_window(qapp)
    controller.cfg.text.punctuation = False
    controller.cfg.ui.overlay_position = "top_center"
    window.refresh()
    assert window.punctuation_toggle.isChecked() is False
    assert window.position_combo.currentData() == "top_center"
    window.close()
