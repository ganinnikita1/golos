"""Мастер первого запуска: приветствие → микрофон → модель → готово.

Показывается один раз, пока ``cfg.onboarding_done == False`` (решение принимает
ядро, UI только предоставляет ``show_onboarding()``). Настройки мастера сразу
применяются через ``controller.apply_config(cfg)``.
"""

from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths
from ..config import AppConfig
from . import formatting, icons, theme
from .widgets import Card, Field, LevelMeter, PageHeader, ToggleSwitch

logger = logging.getLogger(__name__)

TOTAL_STEPS = 4
STEP_TITLES = ("Знакомство", "Микрофон", "Модель", "Готово")


class _SidePanel(QWidget):
    """Левая акцентная панель с шагами мастера."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(286)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 30, 22, 24)
        layout.setSpacing(6)

        logo = QLabel(self)
        logo.setPixmap(icons.app_pixmap(56))
        logo.setFixedSize(56, 56)
        layout.addWidget(logo)
        layout.addSpacing(14)

        title = QLabel("Голос", self)
        title.setStyleSheet("font-size: 26px; font-weight: 600; color: #F3F8F7;")
        layout.addWidget(title)
        tagline = QLabel("Диктовка на русском —\nофлайн и без лишних движений.", self)
        tagline.setStyleSheet("font-size: 13px; color: #A9C6C1; line-height: 140%;")
        layout.addWidget(tagline)
        layout.addSpacing(26)

        self.steps: list[_StepRow] = []
        for index, text in enumerate(STEP_TITLES):
            row = _StepRow(index + 1, text, self)
            self.steps.append(row)
            layout.addWidget(row)
        layout.addStretch(1)

        hint = QLabel(
            "Всё можно изменить позже\nв настройках из трея.",
            self,
        )
        hint.setStyleSheet("font-size: 12px; color: #7E9B97;")
        layout.addWidget(hint)
        footer = QLabel(f"версия {__version__}", self)
        footer.setStyleSheet("font-size: 11px; color: #6B8783;")
        layout.addWidget(footer)

    def set_step(self, current: int) -> None:
        for index, row in enumerate(self.steps):
            if index < current:
                row.set_state("done")
            elif index == current:
                row.set_state("current")
            else:
                row.set_state("pending")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#1C403C"))
        gradient.setColorAt(1.0, QColor("#101A1E"))
        painter.fillRect(self.rect(), gradient)
        # Декоративные окружности.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(79, 209, 197, 14))
        painter.drawEllipse(QRectF(self.width() - 130, -80, 230, 230))
        painter.setBrush(QColor(79, 209, 197, 8))
        painter.drawEllipse(QRectF(-90, self.height() - 180, 260, 260))
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1))
        painter.drawLine(0, 0, 0, self.height())
        painter.end()


class _StepRow(QWidget):
    """Строка шага: номер/галочка + название."""

    def __init__(self, number: int, title: str, parent=None):
        super().__init__(parent)
        self.number = number
        self.title = title
        self._state = "pending"
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QRectF(0, (self.height() - 24) / 2.0, 24, 24)
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        if self._state == "current":
            painter.setBrush(QColor("#4FD1C5"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center)
            painter.setPen(QColor("#07211E"))
            painter.drawText(center, int(Qt.AlignmentFlag.AlignCenter), str(self.number))
            text_color = QColor("#F3F8F7")
        elif self._state == "done":
            painter.setBrush(QColor(79, 209, 197, 40))
            painter.setPen(QPen(QColor("#4FD1C5"), 1.2))
            painter.drawEllipse(center)
            check = QPen(QColor("#4FD1C5"), 2.0)
            check.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(check)
            painter.drawLine(center.left() + 6, center.center().y(), center.center().x() - 1, center.bottom() - 7)
            painter.drawLine(center.center().x() - 1, center.bottom() - 7, center.right() - 6, center.top() + 7)
            text_color = QColor("#C6DDD9")
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 42), 1.2))
            painter.drawEllipse(center)
            painter.setPen(QColor("#8FA8A4"))
            painter.drawText(center, int(Qt.AlignmentFlag.AlignCenter), str(self.number))
            text_color = QColor("#8FA8A4")

        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(13)
        if self._state == "current":
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(38, 0, self.width() - 38, self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.title,
        )
        painter.end()


class OnboardingWizard(QDialog):
    """Мастер первого запуска на четыре шага."""

    completed = Signal(object)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._cfg = copy.deepcopy(self._read_config())
        self._step = 0
        self._model_loaded = False
        self._test_active = False
        self.setWindowTitle("Первый запуск — Голос")
        self.setWindowIcon(icons.app_icon())
        self.resize(840, 580)
        self.setMinimumSize(780, 540)

        self._build_ui()
        self._load_values()
        self._refresh_model_status()
        self._update_steps()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self._refresh_model_status)

    # ------------------------------------------------------------------ API
    def current_step(self) -> int:
        return self._step

    def go_to_step(self, index: int) -> None:
        """Перейти на шаг (используется кнопками и скриншотами)."""
        index = max(0, min(TOTAL_STEPS - 1, int(index)))
        self._step = index
        self.stack.setCurrentIndex(index)
        self._update_steps()

    def on_progress(self, message: str, fraction) -> None:
        """Событие progress из шины во время скачивания модели."""
        if message:
            self.model_status.setText(message)
            self.model_status.setStyleSheet(f"color: {theme.current().accent};")
        if fraction is None:
            self.model_progress.setVisible(False)
            return
        try:
            value = int(max(0.0, min(1.0, float(fraction))) * 100)
        except (TypeError, ValueError):
            return
        self.model_progress.setVisible(True)
        self.model_progress.setValue(value)
        if value >= 100:
            self._model_loaded = True
            QTimer.singleShot(600, lambda: self.model_progress.setVisible(False))

    # ------------------------------------------------------------ построение
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.side = _SidePanel(self)
        root.addWidget(self.side)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        root.addLayout(right, 1)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 26, 30, 10)
        content_layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.addStretch(1)
        self.step_label = QLabel("Шаг 1 из 4", content)
        self.step_label.setObjectName("Faint")
        top_row.addWidget(self.step_label)
        content_layout.addLayout(top_row)

        self.stack = QStackedWidget(content)
        self.stack.addWidget(self._build_welcome_page())
        self.stack.addWidget(self._build_microphone_page())
        self.stack.addWidget(self._build_model_page())
        self.stack.addWidget(self._build_done_page())
        content_layout.addWidget(self.stack, 1)
        right.addWidget(content, 1)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(30, 6, 30, 20)
        footer_layout.setSpacing(10)
        self.skip_button = QPushButton("Пропустить настройку", footer)
        self.skip_button.setObjectName("Ghost")
        self.skip_button.clicked.connect(self._skip)
        footer_layout.addWidget(self.skip_button)
        footer_layout.addStretch(1)
        self.back_button = QPushButton("Назад", footer)
        self.back_button.setObjectName("Ghost")
        self.back_button.clicked.connect(self._go_back)
        self.next_button = QPushButton("Далее", footer)
        self.next_button.setObjectName("Primary")
        self.next_button.setMinimumWidth(130)
        self.next_button.clicked.connect(self._go_next)
        footer_layout.addWidget(self.back_button)
        footer_layout.addWidget(self.next_button)
        right.addWidget(footer)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        return page, layout

    def _build_welcome_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            PageHeader(
                "Привет! Это «Голос»",
                "Приложение превращает речь в текст и вставляет его в активное окно. "
                "Работает офлайн — аудио не покидает компьютер.",
            )
        )
        features = (
            "Распознавание GigaAM-v3 прямо на процессоре.",
            "Вызов из любого приложения горячей клавишей.",
            "Аккуратная пунктуация и вставка в активное поле.",
        )
        for text in features:
            layout.addWidget(_feature_row(text, page))

        hotkey_card = Card()
        label = QLabel("Горячая клавиша по умолчанию", hotkey_card)
        label.setObjectName("FieldLabel")
        hotkey_card.add(label)
        value = QLabel("Ctrl+Alt+Space", hotkey_card)
        value.setStyleSheet(
            "font-family: 'Cascadia Mono', Consolas; font-size: 22px; font-weight: 600;"
        )
        hotkey_card.add(value)
        hint = QLabel("Нажали — говорите, нажали ещё раз — готово.", hotkey_card)
        hint.setObjectName("FieldHint")
        hotkey_card.add(hint)
        self.welcome_hotkey_label = value
        layout.addWidget(hotkey_card)
        layout.addStretch(1)
        return page

    def _build_microphone_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            PageHeader(
                "Выберите микрофон",
                "Это устройство будет записывать речь. Проверьте уровень сигнала.",
            )
        )
        card = Card("Устройство записи")
        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        self.device_combo = QComboBox(card)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refresh = QPushButton("Обновить", card)
        refresh.clicked.connect(self._reload_devices)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(refresh)
        field = Field("Микрофон", None, "Оставьте системный — Windows выберет устройство сама.")
        field.layout().addLayout(device_row)
        card.add(field)
        layout.addWidget(card)

        test_card = Card("Проверка")
        test_row = QHBoxLayout()
        test_row.setSpacing(12)
        self.test_button = QPushButton("Проверить микрофон", test_card)
        self.test_button.clicked.connect(self._test_microphone)
        test_row.addWidget(self.test_button)
        self.level_meter = LevelMeter(bars=24, bar_width=4, gap=3, height=30, parent=test_card)
        test_row.addWidget(self.level_meter, 1)
        test_card.add_layout(test_row)
        self.test_status = QLabel("Нажмите кнопку и скажите несколько слов.", test_card)
        self.test_status.setObjectName("FieldHint")
        self.test_status.setWordWrap(True)
        test_card.add(self.test_status)
        layout.addWidget(test_card)
        layout.addStretch(1)
        return page

    def _build_model_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            PageHeader(
                "Скачаем модель распознавания",
                "GigaAM-v3 работает локально на процессоре. Модель скачивается один раз "
                "и дальше используется без интернета.",
            )
        )
        card = Card()
        name = QLabel("GigaAM-v3 · e2e_rnnt", card)
        name.setObjectName("Heading")
        card.add(name)
        path = paths.display_path(paths.models_dir() / self._cfg.asr.gigaam_dirname)
        path_label = QLabel(f"Модель будет сохранена в {path}", card)
        path_label.setObjectName("FieldHint")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.add(path_label)
        hint = QLabel(
            "Для скачивания нужен интернет. После загрузки модель работает офлайн.",
            card,
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        card.add(hint)

        action_row = QHBoxLayout()
        self.load_model_button = QPushButton("Скачать и загрузить", card)
        self.load_model_button.setObjectName("Primary")
        self.load_model_button.clicked.connect(self._load_model)
        action_row.addWidget(self.load_model_button)
        action_row.addStretch(1)
        card.add_layout(action_row)
        self.model_progress = QProgressBar(card)
        self.model_progress.setRange(0, 100)
        self.model_progress.setVisible(False)
        card.add(self.model_progress)
        self.model_status = QLabel("Модель ещё не загружена.", card)
        self.model_status.setObjectName("FieldHint")
        self.model_status.setWordWrap(True)
        card.add(self.model_status)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_done_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            PageHeader(
                "Всё готово",
                "Нажмите горячую клавишу в любом приложении и говорите — текст появится "
                "в активном окне.",
            )
        )
        card = Card("Ваши настройки")
        self.summary_hotkey = _summary_row(card, "Горячая клавиша", "")
        self.summary_mode = _summary_row(card, "Режим", "")
        self.summary_model = _summary_row(card, "Модель", "")
        layout.addWidget(card)

        options_card = Card("Удобство")
        row, self.autostart_toggle = _toggle_row(
            "Запускать при включении компьютера",
            "Приложение будет тихо работать в трее.",
            self._cfg.ui.autostart,
            options_card,
        )
        options_card.add(row)
        row, self.overlay_toggle = _toggle_row(
            "Показывать оверлей во время диктовки",
            "Пилюля с таймером и текстом поверх окон.",
            self._cfg.ui.overlay_enabled,
            options_card,
        )
        options_card.add(row)
        layout.addWidget(options_card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- данные
    def _read_config(self) -> AppConfig:
        try:
            return self.controller.get_config()
        except Exception:
            logger.exception("Не удалось прочитать конфигурацию для мастера")
            from ..config import default_config

            return default_config()

    def _load_values(self) -> None:
        cfg = self._cfg
        self.welcome_hotkey_label.setText(formatting.format_hotkey(cfg.hotkey.hotkey))
        self.summary_hotkey.setText(
            formatting.format_hotkey(cfg.hotkey.hotkey)
        )
        self.summary_mode.setText(
            "Тумблер" if cfg.hotkey.mode == "toggle" else "Удержание клавиши"
        )
        self.summary_model.setText(formatting.model_title(cfg))
        self.autostart_toggle.setChecked(cfg.ui.autostart)
        self.overlay_toggle.setChecked(cfg.ui.overlay_enabled)
        self._reload_devices()

    def _reload_devices(self) -> None:
        current = self._cfg.audio.input_device or ""
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("Системный микрофон по умолчанию", "")
        try:
            devices = self.controller.get_devices() or []
        except Exception as exc:
            logger.warning("Не удалось получить микрофоны: %s", exc)
            devices = []
        for device in devices:
            label = device.name
            if getattr(device, "is_default", False):
                label += " (по умолчанию)"
            self.device_combo.addItem(label, device.name)
        index = self.device_combo.findData(current)
        self.device_combo.setCurrentIndex(index if index >= 0 else 0)
        self.device_combo.blockSignals(False)

    # ------------------------------------------------------------- шаги
    def _update_steps(self) -> None:
        self.side.set_step(self._step)
        self.step_label.setText(f"Шаг {self._step + 1} из {TOTAL_STEPS}")
        self.back_button.setVisible(self._step > 0)
        self.skip_button.setVisible(self._step < TOTAL_STEPS - 1)
        self.next_button.setText("Начать пользоваться" if self._step == TOTAL_STEPS - 1 else "Далее")
        if self._step == 2:
            self._refresh_model_status()
        if hasattr(self, "_poll_timer"):
            if self._step in {2, 3}:
                self._poll_timer.start()
            else:
                self._poll_timer.stop()

    def _go_back(self) -> None:
        self.go_to_step(self._step - 1)

    def _go_next(self) -> None:
        if self._step >= TOTAL_STEPS - 1:
            self._finish()
            return
        self.go_to_step(self._step + 1)

    def _skip(self) -> None:
        self._mark_done(apply=True)
        self.accept()

    def _finish(self) -> None:
        cfg = self._mark_done(apply=True)
        self.completed.emit(cfg)
        self.accept()

    def _mark_done(self, apply: bool) -> AppConfig:
        cfg = copy.deepcopy(self._cfg)
        cfg.onboarding_done = True
        cfg.ui.autostart = self.autostart_toggle.isChecked()
        cfg.ui.overlay_enabled = self.overlay_toggle.isChecked()
        cfg.audio.input_device = self.device_combo.currentData() or None
        if apply:
            try:
                self.controller.apply_config(cfg, save=True)
            except Exception as exc:
                logger.exception("Не удалось сохранить настройки мастера")
                self.model_status.setText(f"Не удалось сохранить настройки: {exc}")
            self._cfg = cfg
        return cfg

    # ------------------------------------------------------------- модель
    def _refresh_model_status(self) -> None:
        try:
            status = self.controller.engine_status() or {}
        except Exception:
            return
        loaded = bool(status.get("loaded"))
        loading = bool(status.get("loading"))
        if loaded:
            self._model_loaded = True
            self.model_status.setText("Модель загружена и готова к работе.")
            self.model_status.setStyleSheet(f"color: {theme.current().success};")
            self.load_model_button.setText("Модель готова")
            self.load_model_button.setEnabled(False)
            self.model_progress.setVisible(False)
        elif loading:
            self.model_status.setText(status.get("message") or "Загружаем модель…")
            self.model_status.setStyleSheet(f"color: {theme.current().accent};")
            self.load_model_button.setText("Загружается…")
            self.load_model_button.setEnabled(False)
        else:
            self.model_status.setText(status.get("message") or "Модель ещё не загружена.")
            self.model_status.setStyleSheet(f"color: {theme.current().muted};")
            self.load_model_button.setText("Скачать и загрузить")
            self.load_model_button.setEnabled(True)

    def _load_model(self) -> None:
        cfg = copy.deepcopy(self._cfg)
        cfg.onboarding_done = True
        try:
            self.controller.apply_config(cfg, save=False)
        except Exception:
            logger.exception("Не удалось применить конфигурацию перед загрузкой модели")
        try:
            self.controller.load_engine()
        except Exception as exc:
            logger.exception("Не удалось запустить загрузку модели")
            self.model_status.setText(f"Не удалось загрузить модель: {exc}")
            self.model_status.setStyleSheet(f"color: {theme.current().danger};")
            return
        self.model_progress.setVisible(True)
        self.model_progress.setValue(0)
        self._refresh_model_status()

    # --------------------------------------------------------- микрофон
    def _test_microphone(self) -> None:
        try:
            result = self.controller.test_microphone()
        except AttributeError:
            self.test_status.setText(
                "Проверка микрофона не поддерживается подключённым ядром."
            )
            return
        except Exception as exc:
            logger.exception("Ошибка проверки микрофона")
            self.test_status.setText(f"Не удалось проверить микрофон: {exc}")
            return
        if result is False:
            self.test_status.setText("Проверка уже идёт — дождитесь завершения.")
            return
        self._test_active = True
        self.test_button.setEnabled(False)
        self.test_button.setText("Проверяем…")
        self.test_status.setText("Говорите — полоска уровня покажет сигнал микрофона.")
        self.level_meter.set_active(True)
        QTimer.singleShot(4000, self._finish_test)

    def _finish_test(self) -> None:
        self._test_active = False
        self.test_button.setEnabled(True)
        self.test_button.setText("Проверить микрофон")
        self.test_status.setText(
            "Проверка завершена. Если индикатор двигался — микрофон работает."
        )
        self.level_meter.set_active(False)

    def on_level(self, value: float) -> None:
        try:
            self.level_meter.set_level(float(value))
        except (TypeError, ValueError):
            return

    # --- события окна ------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._refresh_model_status()
        if self._step in {2, 3}:
            self._poll_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._poll_timer.stop()
        super().hideEvent(event)


# --------------------------------------------------------------------------- #
# Мелкие помощники страниц
# --------------------------------------------------------------------------- #
def _feature_row(text: str, parent: QWidget) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    dot = QLabel(row)
    dot.setFixedSize(18, 18)
    dot.setStyleSheet(
        f"background: {theme.current().accent}; border-radius: 9px;"
    )
    layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(text, row)
    label.setWordWrap(True)
    label.setObjectName("Muted")
    layout.addWidget(label, 1)
    return row


def _summary_row(card: Card, title: str, value: str) -> QLabel:
    row = QWidget(card)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    label = QLabel(title, row)
    label.setObjectName("Muted")
    layout.addWidget(label)
    layout.addStretch(1)
    value_label = QLabel(value, row)
    value_label.setStyleSheet("font-weight: 600;")
    layout.addWidget(value_label)
    card.add(row)
    return value_label


def _toggle_row(
    title: str,
    hint: str,
    checked: bool,
    parent: QWidget,
) -> tuple[QWidget, ToggleSwitch]:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    title_label = QLabel(title, row)
    texts.addWidget(title_label)
    hint_label = QLabel(hint, row)
    hint_label.setObjectName("FieldHint")
    hint_label.setWordWrap(True)
    texts.addWidget(hint_label)
    layout.addLayout(texts, 1)
    toggle = ToggleSwitch(row)
    toggle.setChecked(checked)
    layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
    return row, toggle


__all__ = ["OnboardingWizard", "TOTAL_STEPS"]
