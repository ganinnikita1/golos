"""Демо-режим интерфейса без микрофона, модели и вставки.

Запуск::

    python -m golos.ui.demo

Поднимает настоящие трей, оверлей и окна, но вместо ядра — ``FakeController``,
который эмулирует все события шины. Панель демо позволяет увидеть каждое
состояние: запись с живым уровнем и текстом, распознавание, полировку,
вставку, результат, ошибку, прогресс загрузки модели.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import random
import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths
from ..config import default_config
from ..controller import DeviceInfo, HistoryItem
from ..events import EventBus
from . import icons
from .app_ui import GolosUI, create_application
from .widgets import Card, PageHeader, Separator

logger = logging.getLogger(__name__)

#: Фраза, которой демо «диктует».
DEMO_PHRASE = (
    "Привет! Это тестовая диктовка в приложении «Голос». "
    "Проверим, как выглядит оверлей, пока идёт запись, а затем вставка готового текста."
)


class FakeController(QObject):
    """Заглушка ядра для UI: генерирует события в шину, как настоящий контроллер."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = EventBus()
        self.cfg = default_config()
        self.cfg.onboarding_done = True
        self.state = "idle"
        self.engine_loaded = False
        self.engine_loading = False
        self._history: list[HistoryItem] = self._seed_history()
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(55)
        self._level_timer.timeout.connect(self._emit_level)
        self._partial_timer = QTimer(self)
        self._partial_timer.setInterval(400)
        self._partial_timer.timeout.connect(self._grow_partial)
        self._partial_words: list[str] = []
        self._partial_index = 0
        self._recording_started = 0.0
        self._demo_level = 0.0
        self._load_step = 0
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(180)
        self._load_timer.timeout.connect(self._load_tick)

    # --- Controller protocol ----------------------------------------------
    def get_config(self):
        return self.cfg

    def apply_config(self, cfg, *, save: bool = True) -> None:
        self.cfg = copy.deepcopy(cfg)
        logger.debug("Демо: конфигурация применена (save=%s)", save)

    def get_devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(index=-1, name="Системный микрофон по умолчанию", is_default=True),
            DeviceInfo(index=0, name="Микрофон (Realtek(R) Audio)", channels=2),
            DeviceInfo(index=1, name="Гарнитура USB Headset", channels=1),
            DeviceInfo(index=2, name="Виртуальный кабель (VB-Audio)", channels=2),
        ]

    def start_recording(self) -> None:
        if self.state in {"recording", "transcribing", "polishing", "inserting", "loading"}:
            return
        self.state = "recording"
        self._demo_level = 0.0
        self._recording_started = time.monotonic()
        self._partial_words = DEMO_PHRASE.split()
        self._partial_index = 0
        self.bus.emit("state", state="recording")
        self._level_timer.start()
        self._partial_timer.start()

    def stop_recording(self) -> None:
        if self.state != "recording":
            return
        self._level_timer.stop()
        self._partial_timer.stop()
        self._finish_recording()

    def cancel(self) -> None:
        if self.state in {"idle", "error"}:
            return
        self._stop_all()
        self.state = "idle"
        self.bus.emit("state", state="idle")
        self.bus.emit("toast", message="Запись отменена", level="info")

    def is_busy(self) -> bool:
        return self.state in {"recording", "transcribing", "polishing", "inserting", "loading"}

    def load_engine(self) -> None:
        if self.engine_loaded or self.engine_loading:
            if self.engine_loaded:
                self.bus.emit("toast", message="Модель уже загружена", level="info")
            return
        self.engine_loading = True
        self._load_step = 0
        self.state = "loading"
        self.bus.emit("state", state="loading")
        self.bus.emit("progress", message="Скачиваем модель GigaAM-v3…", fraction=0.0)
        self._load_timer.start()

    def engine_status(self) -> dict:
        if self.engine_loading:
            message = "Загружаем GigaAM-v3…"
        elif self.engine_loaded:
            message = "Модель загружена и готова к работе"
        else:
            message = "Модель не загружена — нажмите «Загрузить модель»"
        return {
            "engine": self.cfg.asr.engine,
            "loaded": self.engine_loaded,
            "loading": self.engine_loading,
            "message": message,
        }

    def history(self) -> list[HistoryItem]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def insert_text(self, text: str) -> bool:
        self.bus.emit("toast", message="Текст отправлен в активное окно (демо)", level="info")
        return True

    def copy_to_clipboard(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text or "")

    def open_config_dir(self) -> None:
        QDesktopServices.openUrl(paths.appdata_dir().as_uri())

    def quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()

    # --- дополнительные методы, которых ждёт UI ---------------------------
    def test_microphone(self) -> bool:
        """Проверка микрофона: 2,5 секунды «живого» уровня."""
        if self.is_busy():
            return False
        self.bus.emit("toast", message="Проверяем микрофон — скажите что-нибудь", level="info")
        self._test_ticks = 0
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(80)
        self._test_timer.timeout.connect(self._test_tick)
        self._test_timer.start()
        return True

    # --- эмуляция ---------------------------------------------------------
    def emit_partial(self, text: str) -> None:
        self.bus.emit("partial", text=text)

    def emit_level(self, value: float) -> None:
        self._demo_level = max(0.0, min(1.0, float(value)))
        self.bus.emit("level", value=self._demo_level)

    def emit_result(self, text: str = DEMO_PHRASE, inserted: bool = True) -> None:
        elapsed = 6.4
        record = HistoryItem(
            ts=time.time(),
            text=text,
            raw_text=text,
            engine=self.cfg.asr.engine,
            duration_s=elapsed,
            rtf=0.12,
            inserted=inserted,
        )
        self._history.insert(0, record)
        self.bus.emit(
            "result",
            text=text,
            raw_text=text,
            elapsed_s=elapsed,
            rtf=0.12,
            engine=self.cfg.asr.engine,
            inserted=inserted,
            duration_s=elapsed,
        )
        self.state = "idle"
        self.bus.emit("state", state="idle")

    def emit_error(self, message: str = "Не удалось распознать запись. Проверьте микрофон.") -> None:
        self._stop_all()
        self.state = "error"
        self.bus.emit("state", state="error")
        self.bus.emit("error", message=message, detail="Демо-ошибка FakeController")

    def emit_toast(self, message: str = "Демонстрационное уведомление", level: str = "info") -> None:
        self.bus.emit("toast", message=message, level=level)

    def emit_progress(self, fraction: float, message: str = "Скачиваем модель…") -> None:
        self.bus.emit("progress", message=message, fraction=max(0.0, min(1.0, fraction)))

    # --- внутреннее -------------------------------------------------------
    def _emit_level(self) -> None:
        if self._demo_level > 0.02:
            value = self._demo_level
        else:
            # Плавный «живой» уровень: несколько синусоид + шум.
            tick = time.monotonic() * 4.0
            value = (
                0.34
                + 0.18 * math.sin(tick)
                + 0.10 * math.sin(tick * 2.7 + 1.3)
                + random.uniform(-0.05, 0.05)
            )
        self.bus.emit("level", value=max(0.03, min(1.0, value)))

    def _grow_partial(self) -> None:
        if self.state != "recording":
            return
        self._partial_index = min(len(self._partial_words), self._partial_index + 2)
        self.bus.emit("partial", text=" ".join(self._partial_words[: self._partial_index]))

    def _finish_recording(self) -> None:
        elapsed = max(0.8, time.monotonic() - self._recording_started)
        self.state = "transcribing"
        self.bus.emit("state", state="transcribing")
        self.bus.emit("partial", text=DEMO_PHRASE)
        QTimer.singleShot(900, lambda: self._advance("polishing"))
        QTimer.singleShot(1600, lambda: self._advance("inserting"))
        QTimer.singleShot(2200, lambda: self._finish_with_result(elapsed))

    def _advance(self, state: str) -> None:
        self.state = state
        self.bus.emit("state", state=state)

    def _finish_with_result(self, elapsed: float) -> None:
        record = HistoryItem(
            ts=time.time(),
            text=DEMO_PHRASE,
            raw_text=DEMO_PHRASE,
            engine=self.cfg.asr.engine,
            duration_s=elapsed,
            rtf=0.11,
            inserted=True,
        )
        self._history.insert(0, record)
        self.state = "idle"
        self.bus.emit(
            "result",
            text=DEMO_PHRASE,
            raw_text=DEMO_PHRASE,
            elapsed_s=elapsed,
            rtf=0.11,
            engine=self.cfg.asr.engine,
            inserted=True,
            duration_s=elapsed,
        )
        self.bus.emit("state", state="idle")

    def _stop_all(self) -> None:
        self._level_timer.stop()
        self._partial_timer.stop()
        self._load_timer.stop()
        if hasattr(self, "_test_timer") and self._test_timer is not None:
            self._test_timer.stop()

    def _load_tick(self) -> None:
        self._load_step += 1
        fraction = min(0.95, self._load_step / 12.0)
        self.bus.emit("progress", message="Скачиваем модель GigaAM-v3…", fraction=fraction)
        if self._load_step % 4 == 0:
            self.bus.emit("toast", message=f"Модель: {int(fraction * 100)} %", level="info")
        if self._load_step >= 13:
            self._load_timer.stop()
            self.engine_loading = False
            self.engine_loaded = True
            self.state = "idle"
            self.bus.emit("progress", message="Модель готова", fraction=1.0)
            self.bus.emit("toast", message="Модель GigaAM-v3 загружена", level="info")
            self.bus.emit("state", state="idle")

    def _test_tick(self) -> None:
        self._test_ticks += 1
        self.bus.emit("level", value=max(0.03, min(1.0, 0.4 + random.uniform(-0.25, 0.4))))
        if self._test_ticks >= 30:
            self._test_timer.stop()
            self.bus.emit("toast", message="Микрофон работает: уровень сигнала в норме", level="info")

    def _seed_history(self) -> list[HistoryItem]:
        now = time.time()
        samples = [
            (
                "Коллеги, добрый день! Напоминаю, что встречу по проекту переносим на четверг, "
                "на одиннадцать часов утра.",
                "gigaam",
                12.4,
                0.10,
                now - 3600 * 2.5,
            ),
            (
                "Нужно добавить в отчёт раздел про метрики за январь и февраль, а затем "
                "отправить его на согласование.",
                "gigaam",
                8.2,
                0.09,
                now - 3600 * 5,
            ),
            (
                "Привет, это напоминание про завтрашний созвон в девять тридцать. "
                "Не забудь подготовить презентацию.",
                "whisper",
                6.7,
                0.31,
                now - 86400 - 3600 * 2,
            ),
            (
                "Список покупок: молоко, хлеб, яйца, сыр, зелень и что-нибудь к чаю.",
                "gigaam",
                4.9,
                0.11,
                now - 86400 * 2,
            ),
            (
                "Проверил интеграцию с локальной моделью распознавания — всё работает офлайн, "
                "задержка в пределах нормы.",
                "gigaam",
                9.6,
                0.13,
                now - 86400 * 4,
            ),
        ]
        return [
            HistoryItem(
                ts=ts,
                text=text,
                raw_text=text,
                engine=engine,
                duration_s=duration,
                rtf=rtf,
                inserted=True,
            )
            for text, engine, duration, rtf, ts in samples
        ]


class DemoPanel(QDialog):
    """Панель управления демо-режимом."""

    def __init__(self, controller: FakeController, ui: GolosUI, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.ui = ui
        self.setWindowTitle("Голос — демо-режим")
        self.setWindowIcon(icons.app_icon())
        self.resize(560, 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(12)
        logo = QLabel(header)
        logo.setPixmap(icons.app_pixmap(44))
        logo.setFixedSize(44, 44)
        header_layout.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Голос", header)
        title.setObjectName("Heading")
        titles.addWidget(title)
        subtitle = QLabel(f"Демо-режим · версия {__version__}", header)
        subtitle.setObjectName("Faint")
        titles.addWidget(subtitle)
        header_layout.addLayout(titles)
        header_layout.addStretch(1)
        outer.addWidget(header)
        outer.addWidget(Separator())

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(16)

        page_header = PageHeader(
            "Проверка интерфейса",
            "Ядро эмулируется: микрофон, модель и вставка текста не используются. "
            "Все события идут через ту же шину, что и в настоящем приложении.",
            content,
        )
        content_layout.addWidget(page_header)

        # Запись.
        record_card = Card("Диктовка", "Полный цикл: запись → распознавание → вставка.", content)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.start_button = QPushButton("Начать запись", record_card)
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.controller.start_recording)
        self.stop_button = QPushButton("Остановить и распознать", record_card)
        self.stop_button.clicked.connect(self.controller.stop_recording)
        self.cancel_button = QPushButton("Отменить", record_card)
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.clicked.connect(self.controller.cancel)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        record_card.add_layout(row)
        content_layout.addWidget(record_card)

        # Уровень.
        level_card = Card("Уровень сигнала", "Ползунок задаёт уровень, который уходит в оверлей.", content)
        level_row = QHBoxLayout()
        self.level_slider = QSlider(Qt.Orientation.Horizontal, level_card)
        self.level_slider.setRange(0, 100)
        self.level_slider.setValue(45)
        self.level_value = QLabel("45 %", level_card)
        self.level_value.setObjectName("Value")
        self.level_value.setMinimumWidth(48)
        self.level_slider.valueChanged.connect(self._on_level_changed)
        level_row.addWidget(self.level_slider, 1)
        level_row.addWidget(self.level_value)
        level_card.add_layout(level_row)
        content_layout.addWidget(level_card)

        # Текст.
        text_card = Card("Текст оверлея", "Отправьте промежуточный текст или готовый результат.", content)
        self.text_edit = QLineEdit(DEMO_PHRASE, text_card)
        self.text_edit.setPlaceholderText("Текст для оверлея")
        self.text_edit.setCursorPosition(0)
        text_card.add(self.text_edit)
        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        partial_button = QPushButton("Показать частичный текст", text_card)
        partial_button.clicked.connect(self._emit_partial)
        result_button = QPushButton("Показать результат", text_card)
        result_button.clicked.connect(self._emit_result)
        text_row.addWidget(partial_button)
        text_row.addWidget(result_button)
        text_row.addStretch(1)
        text_card.add_layout(text_row)
        content_layout.addWidget(text_card)

        # Служебные события.
        events_card = Card("Служебные события", "Ошибки, прогресс модели и уведомления.", content)
        events_row = QHBoxLayout()
        events_row.setSpacing(8)
        error_button = QPushButton("Ошибка", events_card)
        error_button.clicked.connect(self._emit_error)
        progress_button = QPushButton("Прогресс модели", events_card)
        progress_button.clicked.connect(self._emit_progress)
        toast_button = QPushButton("Уведомление", events_card)
        toast_button.clicked.connect(self._emit_toast)
        events_row.addWidget(error_button)
        events_row.addWidget(progress_button)
        events_row.addWidget(toast_button)
        events_row.addStretch(1)
        events_card.add_layout(events_row)
        content_layout.addWidget(events_card)

        # Окна.
        windows_card = Card("Окна приложения", "Проверить окна можно поверх работающего трея.", content)
        windows_row = QHBoxLayout()
        windows_row.setSpacing(8)
        settings_button = QPushButton("Настройки", windows_card)
        settings_button.clicked.connect(self.ui.show_settings)
        history_button = QPushButton("История", windows_card)
        history_button.clicked.connect(self.ui.show_history)
        onboarding_button = QPushButton("Мастер первого запуска", windows_card)
        onboarding_button.clicked.connect(self.ui.show_onboarding)
        windows_row.addWidget(settings_button)
        windows_row.addWidget(history_button)
        windows_row.addWidget(onboarding_button)
        windows_row.addStretch(1)
        windows_card.add_layout(windows_row)
        content_layout.addWidget(windows_card)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 12)
        footer_layout.setSpacing(8)
        hint = QLabel(
            "Приложение остаётся в трее: левая кнопка — начать/остановить диктовку.",
            footer,
        )
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        footer_layout.addWidget(hint, 1)
        quit_button = QPushButton("Выход", footer)
        quit_button.setObjectName("Ghost")
        quit_button.clicked.connect(self._quit)
        footer_layout.addWidget(quit_button)
        outer.addWidget(footer)

    # --- действия демо -----------------------------------------------------
    def _on_level_changed(self, value: int) -> None:
        self.level_value.setText(f"{value} %")
        self.controller.emit_level(value / 100.0)

    def _emit_partial(self) -> None:
        self.controller.emit_partial(self.text_edit.text())

    def _emit_result(self) -> None:
        self.controller.emit_result(self.text_edit.text(), inserted=True)

    def _emit_error(self) -> None:
        self.controller.emit_error()

    def _emit_progress(self) -> None:
        self.controller.load_engine()

    def _emit_toast(self) -> None:
        self.controller.emit_toast("Так выглядит короткое уведомление в трее", "info")

    def _quit(self) -> None:
        self.ui.shutdown()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.ui.shutdown()
        super().closeEvent(event)
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = create_application(list(argv or []))
    app.setQuitOnLastWindowClosed(False)
    controller = FakeController()
    ui = GolosUI(controller)
    ui.start()
    panel = DemoPanel(controller, ui)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    # Удобно для smoke-проверок: GOLOS_DEMO_AUTOQUIT_MS=3000 закрывает демо само.
    autoquit = os.environ.get("GOLOS_DEMO_AUTOQUIT_MS")
    if autoquit and autoquit.isdigit():
        QTimer.singleShot(int(autoquit), app.quit)
    logger.info("Демо-режим запущен. Трей активен, панель управления открыта.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
