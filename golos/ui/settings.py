"""Окно настроек «Голоса».

Слева — навигация по разделам, справа — прокручиваемые страницы с карточками.
Все изменения копятся в копии ``AppConfig`` и применяются кнопкой «Сохранить»
через ``controller.apply_config(cfg)``; отмена возвращает прежнюю тему.
"""

from __future__ import annotations

import copy
import logging
import os
import platform
import sys

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths
from ..config import AppConfig, DictionaryEntry
from . import formatting, icons, theme
from .widgets import (
    Card,
    ElidedLabel,
    Field,
    HotkeyEdit,
    LevelMeter,
    PageHeader,
    ToggleSwitch,
    copy_text,
)

logger = logging.getLogger(__name__)

#: Разделы настроек: (подпись навигации, заголовок страницы, подзаголовок).
NAV_ITEMS = (
    ("Основные", "Основные", "Горячая клавиша, запуск и оформление приложения."),
    ("Микрофон", "Микрофон", "Устройство записи и параметры аудио."),
    ("Распознавание", "Распознавание", "Движок, модель и язык распознавания речи."),
    ("Текст", "Текст", "Пунктуация, регистр и словарь замен после распознавания."),
    ("Полировка LLM", "Полировка LLM", "Аккуратная правка текста нейросетью — по желанию."),
    ("О программе", "О программе", "Информация о приложении и путях к данным."),
)

ENGINE_OPTIONS = (
    ("GigaAM-v3 — офлайн, на CPU", "gigaam"),
    ("faster-whisper — офлайн", "whisper"),
    ("Облачный сервис (OpenAI-совместимый)", "cloud"),
)

GIGAAM_VARIANTS = (
    ("e2e_rnnt — текст с пунктуацией (рекомендуется)", "e2e_rnnt"),
    ("e2e_ctc — текст с пунктуацией, быстрее", "e2e_ctc"),
    ("rnnt — сырой текст без пунктуации", "rnnt"),
    ("ctc — сырой текст, самый быстрый", "ctc"),
    ("ssl — языковая модель без распознавания", "ssl"),
)

WHISPER_MODELS = (
    ("large-v3-turbo — лучшее качество / скорость", "large-v3-turbo"),
    ("large-v3", "large-v3"),
    ("medium", "medium"),
    ("small", "small"),
    ("base", "base"),
    ("tiny", "tiny"),
)

COMPUTE_TYPES = (
    ("int8 — быстрее на CPU (рекомендуется)", "int8"),
    ("float16 — быстрее на GPU", "float16"),
    ("float32 — максимальная точность", "float32"),
)

CLOUD_PROVIDERS = (
    ("OpenAI", "openai"),
    ("Groq", "groq"),
    ("Другой OpenAI-совместимый сервис", "custom"),
)

POLISH_PROVIDERS = (
    ("DeepSeek", "deepseek"),
    ("OpenAI", "openai"),
    ("Другой OpenAI-совместимый сервис", "custom"),
)

THEME_OPTIONS = (
    ("Тёмная", "dark"),
    ("Светлая", "light"),
    ("Как в системе", "system"),
)

POSITION_OPTIONS = (
    ("По центру снизу", "bottom_center"),
    ("По центру сверху", "top_center"),
    ("Справа снизу", "bottom_right"),
    ("Рядом с курсором", "near_cursor"),
)

VAD_OPTIONS = (
    ("Silero — нейросеть, точнее", "silero"),
    ("По уровню энергии — проще", "energy"),
    ("Выключено — писать всё подряд", "off"),
)

NORMALIZE_OPTIONS = (
    ("По пику — выравнивать громкость", "peak"),
    ("По RMS — средний уровень", "rms"),
    ("Выключено", "off"),
)

STYLE_OPTIONS = (
    ("Аккуратная правка — пунктуация и явные ошибки", "dictation"),
    ("Только знаки — минимальные изменения", "literal"),
)


def _combo(options, current: str | None = None, parent=None) -> QComboBox:
    """Комбобокс из (подпись, значение)."""
    combo = QComboBox(parent)
    for label, value in options:
        combo.addItem(label, value)
    if current is not None:
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
    return combo


def _spin(
    minimum: int,
    maximum: int,
    value: int,
    suffix: str = "",
    step: int = 1,
    parent=None,
) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSingleStep(step)
    if suffix:
        spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    return spin


def _line(value: str = "", placeholder: str = "", parent=None) -> QLineEdit:
    line = QLineEdit(value, parent)
    if placeholder:
        line.setPlaceholderText(placeholder)
    return line


class SettingsWindow(QDialog):
    """Окно настроек: навигация слева, страницы справа, сохранение внизу."""

    saved = Signal(object)
    theme_preview_requested = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._cfg = copy.deepcopy(self._read_config())
        self._original = copy.deepcopy(self._cfg)
        self._loading = False
        self._test_active = False

        self.setWindowTitle("Настройки — Голос")
        self.setWindowIcon(icons.app_icon())
        self.resize(900, 640)
        self.setMinimumSize(860, 600)

        self._build_ui()
        self._load_from_config()
        self._refresh_engine_status()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1200)
        self._status_timer.timeout.connect(self._refresh_engine_status)

    # ------------------------------------------------------------------ API
    def set_page(self, index: int) -> None:
        """Переключить раздел настроек (используется скриншотами и тестами)."""
        index = max(0, min(len(NAV_ITEMS) - 1, int(index)))
        self.nav.setCurrentRow(index)
        self.stack.setCurrentIndex(index)

    def current_page(self) -> int:
        return self.stack.currentIndex()

    def refresh(self) -> None:
        """Перечитать конфигурацию из ядра и показать актуальные значения."""
        self._cfg = copy.deepcopy(self._read_config())
        self._original = copy.deepcopy(self._cfg)
        self._load_from_config()
        self._refresh_engine_status()
        self.set_status_message("Настройки загружены", "muted")

    def on_level(self, value: float) -> None:
        """Уровень сигнала с шины событий (проверка микрофона и запись)."""
        try:
            self.mic_meter.set_level(float(value))
        except (TypeError, ValueError):
            return

    def on_progress(self, message: str, fraction) -> None:
        """Прогресс загрузки модели (событие progress из шины)."""
        if message:
            self.asr_status.setText(message)
            self.asr_status.setStyleSheet(f"color: {theme.current().accent};")
        if fraction is None:
            self.asr_progress.setVisible(False)
            return
        try:
            value = int(max(0.0, min(1.0, float(fraction))) * 100)
        except (TypeError, ValueError):
            return
        self.asr_progress.setVisible(True)
        self.asr_progress.setValue(value)
        if value >= 100:
            QTimer.singleShot(800, lambda: self.asr_progress.setVisible(False))

    def set_status_message(self, text: str, role: str = "muted") -> None:
        palette = theme.current()
        color = {
            "danger": palette.danger,
            "warning": palette.warning,
            "success": palette.success,
            "accent": palette.accent,
            "muted": palette.muted,
        }.get(role, palette.muted)
        self.status_line.set_color(theme.qcolor(color))
        self.status_line.setText(text)

    # ------------------------------------------------------------ построение
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        body.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        body.addLayout(right, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_main_page())
        self.stack.addWidget(self._build_microphone_page())
        self.stack.addWidget(self._build_recognition_page())
        self.stack.addWidget(self._build_text_page())
        self.stack.addWidget(self._build_polish_page())
        self.stack.addWidget(self._build_about_page())
        right.addWidget(self.stack, 1)
        right.addWidget(self._build_footer())

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(self)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(208)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(9)
        logo = QLabel(sidebar)
        logo.setPixmap(icons.app_pixmap(28))
        logo.setFixedSize(28, 28)
        brand.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel("Голос", sidebar)
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        brand_text.addWidget(title)
        subtitle = QLabel("Настройки", sidebar)
        subtitle.setObjectName("Faint")
        brand_text.addWidget(subtitle)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(10)

        self.nav = QListWidget(sidebar)
        self.nav.setObjectName("Nav")
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for label, _, _ in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 40))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav)
        layout.addStretch(1)

        version = QLabel(f"версия {__version__}", sidebar)
        version.setObjectName("Faint")
        layout.addWidget(version)
        return sidebar

    def _build_footer(self) -> QWidget:
        footer = QFrame(self)
        footer.setObjectName("Footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(12)
        self.status_line = ElidedLabel("", None, footer)
        self.status_line.setMinimumHeight(22)
        layout.addWidget(self.status_line, 1)
        cancel = QPushButton("Отмена", footer)
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить", footer)
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        layout.addWidget(cancel)
        layout.addWidget(save)
        return footer

    def _make_page(self, index: int):
        header_title = NAV_ITEMS[index][1]
        header_subtitle = NAV_ITEMS[index][2]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(header_title, header_subtitle))
        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll, layout

    def _toggle_row(self, title: str, hint: str, checked: bool) -> tuple[QWidget, ToggleSwitch]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_label = QLabel(title, row)
        title_label.setStyleSheet("font-size: 13px;")
        texts.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint, row)
            hint_label.setObjectName("FieldHint")
            hint_label.setWordWrap(True)
            texts.addWidget(hint_label)
        layout.addLayout(texts, 1)
        toggle = ToggleSwitch(row)
        toggle.setChecked(checked)
        layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        return row, toggle

    # --- страница «Основные» ----------------------------------------------
    def _build_main_page(self) -> QWidget:
        page, layout = self._make_page(0)

        hotkey_card = Card("Горячая клавиша")
        self.hotkey_edit = HotkeyEdit(self._cfg.hotkey.hotkey, parent=hotkey_card)
        hotkey_card.add(
            Field(
                "Сочетание",
                self.hotkey_edit,
                "Нажмите сочетание прямо в поле — оно запишется. Esc — очистить.",
            )
        )
        self.mode_combo = _combo(
            (
                ("Тумблер: нажал — запись, нажал ещё раз — стоп", "toggle"),
                ("Удержание: держите клавишу, пока говорите", "push_to_talk"),
            ),
            self._cfg.hotkey.mode,
            hotkey_card,
        )
        hotkey_card.add(Field("Режим работы", self.mode_combo))
        self.cancel_edit = HotkeyEdit(self._cfg.hotkey.cancel_key, single_key=True, parent=hotkey_card)
        hotkey_card.add(
            Field(
                "Клавиша отмены",
                self.cancel_edit,
                "Отменяет текущую запись без вставки текста. По умолчанию — Esc.",
            )
        )
        layout.addWidget(hotkey_card)

        insert_card = Card("Вставка текста и буфер обмена")
        self.insert_method_combo = _combo(
            (
                ("Без буфера обмена — прямой ввод (приватно, чуть медленнее)", "type"),
                ("Через буфер обмена — быстро и совместимо", "clipboard"),
            ),
            self._cfg.insert.method,
            insert_card,
        )
        insert_card.add(
            Field(
                "Способ вставки",
                self.insert_method_combo,
                "В режиме прямого ввода буфер обмена не используется вообще. "
                "В режиме буфера распознанный текст не остаётся в буфере: "
                "прежнее содержимое возвращается сразу после вставки.",
            )
        )
        self.insert_method_combo.currentIndexChanged.connect(self._on_insert_method_changed)
        row, self.exclude_history_toggle = self._toggle_row(
            "Не сохранять диктовку в журнале буфера обмена",
            "Win+V и облачная синхронизация Windows не запомнят распознанный текст.",
            self._cfg.insert.exclude_from_history,
        )
        insert_card.add(row)
        row, self.restore_clipboard_toggle = self._toggle_row(
            "Возвращать прежнее содержимое буфера",
            "Перед вставкой буфер запоминается и возвращается после — ваши копии не теряются.",
            self._cfg.insert.restore_clipboard,
        )
        insert_card.add(row)
        layout.addWidget(insert_card)

        launch_card = Card("Запуск и поведение")
        row, self.autostart_toggle = self._toggle_row(
            "Запускать при входе в Windows",
            "Приложение будет тихо стартовать в трее.",
            self._cfg.ui.autostart,
        )
        launch_card.add(row)
        row, self.start_minimized_toggle = self._toggle_row(
            "Запускать свёрнутым в трей",
            "Без окна настроек при старте.",
            self._cfg.ui.start_minimized,
        )
        launch_card.add(row)
        row, self.sounds_toggle = self._toggle_row(
            "Звуковые сигналы",
            "Короткий сигнал в начале и в конце записи.",
            self._cfg.ui.sounds,
        )
        launch_card.add(row)
        row, self.history_after_insert_toggle = self._toggle_row(
            "Открывать историю после вставки",
            "Показывать список последних распознаваний.",
            self._cfg.ui.show_history_after_insert,
        )
        launch_card.add(row)
        row, self.history_enabled_toggle = self._toggle_row(
            "Хранить историю распознаваний",
            "Тексты сохраняются локально в %APPDATA%\\Golos\\history.jsonl. "
            "Отключите, чтобы новые записи не сохранялись (старые можно удалить "
            "в окне истории).",
            self._cfg.history_enabled,
        )
        launch_card.add(row)
        layout.addWidget(launch_card)

        overlay_card = Card("Оверлей записи")
        row, self.overlay_toggle = self._toggle_row(
            "Показывать оверлей при диктовке",
            "Пилюля с уровнем, таймером и текстом поверх всех окон.",
            self._cfg.ui.overlay_enabled,
        )
        overlay_card.add(row)
        self.position_combo = _combo(POSITION_OPTIONS, self._cfg.ui.overlay_position, overlay_card)
        overlay_card.add(Field("Положение на экране", self.position_combo))
        self.overlay_toggle.toggled.connect(self.position_combo.setEnabled)
        self.position_combo.setEnabled(self._cfg.ui.overlay_enabled)
        layout.addWidget(overlay_card)

        look_card = Card("Оформление")
        self.theme_combo = _combo(THEME_OPTIONS, self._cfg.ui.theme, look_card)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        look_card.add(Field("Тема", self.theme_combo))
        self.language_combo = _combo((("Русский", "ru"),), self._cfg.ui.language, look_card)
        self.language_combo.setEnabled(False)
        look_card.add(
            Field(
                "Язык интерфейса",
                self.language_combo,
                "Интерфейс приложения полностью на русском языке.",
            )
        )
        layout.addWidget(look_card)
        return page

    # --- страница «Микрофон» ----------------------------------------------
    def _build_microphone_page(self) -> QWidget:
        page, layout = self._make_page(1)

        device_card = Card("Устройство записи")
        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        self.device_combo = QComboBox(device_card)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        refresh_button = QPushButton("Обновить", device_card)
        refresh_button.clicked.connect(self._reload_devices)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(refresh_button)
        field = Field(
            "Микрофон",
            None,
            "По умолчанию используется системный микрофон Windows.",
        )
        field.layout().addLayout(device_row)
        device_card.add(field)
        layout.addWidget(device_card)

        test_card = Card("Проверка")
        test_row = QHBoxLayout()
        test_row.setSpacing(12)
        self.test_button = QPushButton("Проверить микрофон", test_card)
        self.test_button.setObjectName("Primary")
        self.test_button.clicked.connect(self._test_microphone)
        test_row.addWidget(self.test_button)
        self.mic_meter = LevelMeter(bars=26, bar_width=4, gap=3, height=30, parent=test_card)
        test_row.addWidget(self.mic_meter, 1)
        test_card.add_layout(test_row)
        self.mic_test_status = QLabel("Нажмите «Проверить микрофон» и скажите несколько слов.", test_card)
        self.mic_test_status.setObjectName("FieldHint")
        self.mic_test_status.setWordWrap(True)
        test_card.add(self.mic_test_status)
        layout.addWidget(test_card)

        audio_card = Card("Параметры аудио")
        self.max_duration_spin = _spin(5, 600, int(self._cfg.audio.max_duration_s), " с", 5, audio_card)
        audio_card.add(
            Field(
                "Максимальная длительность записи",
                self.max_duration_spin,
                "После этого запись остановится автоматически.",
            )
        )
        self.vad_combo = _combo(VAD_OPTIONS, self._cfg.audio.vad, audio_card)
        audio_card.add(
            Field(
                "Определение речи",
                self.vad_combo,
                "Отсекает тишину в начале и конце записи.",
            )
        )
        self.normalize_combo = _combo(NORMALIZE_OPTIONS, self._cfg.audio.normalize, audio_card)
        audio_card.add(
            Field(
                "Нормализация громкости",
                self.normalize_combo,
                "Выравнивает тихую и громкую речь перед распознаванием.",
            )
        )
        layout.addWidget(audio_card)
        self._reload_devices()
        return page

    # --- страница «Распознавание» -----------------------------------------
    def _build_recognition_page(self) -> QWidget:
        page, layout = self._make_page(2)

        engine_card = Card("Движок распознавания")
        self.engine_combo = _combo(ENGINE_OPTIONS, self._cfg.asr.engine, engine_card)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_card.add(Field("Движок", self.engine_combo))
        self.engine_hint = QLabel("", engine_card)
        self.engine_hint.setObjectName("FieldHint")
        self.engine_hint.setWordWrap(True)
        engine_card.add(self.engine_hint)
        layout.addWidget(engine_card)

        # --- GigaAM-v3 ---
        self.gigaam_card = Card("GigaAM-v3", "Локальная модель распознавания русского языка.")
        self.gigaam_variant_combo = _combo(
            GIGAAM_VARIANTS, self._cfg.asr.gigaam_variant, self.gigaam_card
        )
        self.gigaam_card.add(Field("Вариант модели", self.gigaam_variant_combo))
        model_path = paths.display_path(
            paths.models_dir() / self._cfg.asr.gigaam_dirname
        )
        path_label = QLabel(str(model_path), self.gigaam_card)
        path_label.setObjectName("Faint")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.gigaam_card.add(Field("Папка модели", path_label))
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.load_button = QPushButton("Загрузить модель", self.gigaam_card)
        self.load_button.setObjectName("Primary")
        self.load_button.clicked.connect(self._load_model)
        action_row.addWidget(self.load_button)
        action_row.addStretch(1)
        self.gigaam_card.add_layout(action_row)
        self.asr_progress = QProgressBar(self.gigaam_card)
        self.asr_progress.setRange(0, 100)
        self.asr_progress.setVisible(False)
        self.gigaam_card.add(self.asr_progress)
        self.asr_status = QLabel("", self.gigaam_card)
        self.asr_status.setObjectName("FieldHint")
        self.asr_status.setWordWrap(True)
        self.gigaam_card.add(self.asr_status)
        layout.addWidget(self.gigaam_card)

        # --- faster-whisper ---
        self.whisper_card = Card("faster-whisper", "Офлайн-модель Whisper в реализации на CTranslate2.")
        self.whisper_model_combo = _combo(
            WHISPER_MODELS, self._cfg.asr.whisper_model, self.whisper_card
        )
        self.whisper_card.add(Field("Модель", self.whisper_model_combo))
        self.compute_combo = _combo(COMPUTE_TYPES, self._cfg.asr.whisper_compute_type, self.whisper_card)
        self.whisper_card.add(Field("Точность вычислений", self.compute_combo))
        self.beam_spin = _spin(1, 5, self._cfg.asr.whisper_beam_size, "", 1, self.whisper_card)
        self.whisper_card.add(
            Field(
                "Beam search",
                self.beam_spin,
                "Больше — точнее и медленнее. Для диктовки достаточно 1.",
            )
        )
        layout.addWidget(self.whisper_card)

        # --- облачный ASR ---
        self.cloud_card = Card("Облачное распознавание", "Аудио уходит на сервер по HTTPS.")
        self.cloud_provider_combo = _combo(
            CLOUD_PROVIDERS, self._cfg.asr.cloud_provider, self.cloud_card
        )
        self.cloud_provider_combo.currentIndexChanged.connect(self._on_cloud_provider_changed)
        self.cloud_card.add(Field("Сервис", self.cloud_provider_combo))
        self.cloud_url_edit = _line(
            self._cfg.asr.cloud_base_url, "https://api.openai.com/v1", self.cloud_card
        )
        self.cloud_card.add(Field("Адрес API", self.cloud_url_edit))
        self.cloud_model_edit = _line(
            self._cfg.asr.cloud_model, "gpt-4o-transcribe", self.cloud_card
        )
        self.cloud_card.add(Field("Модель", self.cloud_model_edit))
        self.cloud_env_edit = _line(
            self._cfg.asr.cloud_api_key_env, "OPENAI_API_KEY", self.cloud_card
        )
        self.cloud_card.add(
            Field(
                "Переменная с API-ключом",
                self.cloud_env_edit,
                "Ключ не сохраняется в конфигурации: приложение читает его из переменной окружения.",
            )
        )
        self.cloud_timeout_spin = _spin(
            5, 120, int(self._cfg.asr.cloud_timeout_s), " с", 5, self.cloud_card
        )
        self.cloud_card.add(Field("Таймаут запроса", self.cloud_timeout_spin))
        self.cloud_env_status = QLabel("", self.cloud_card)
        self.cloud_env_status.setObjectName("FieldHint")
        self.cloud_env_status.setWordWrap(True)
        self.cloud_card.add(self.cloud_env_status)
        self.cloud_env_edit.textChanged.connect(self._update_cloud_env_status)
        layout.addWidget(self.cloud_card)

        language_card = Card("Язык")
        self.language_asr_combo = _combo(
            (("Русский", "ru"), ("Определять автоматически", "auto")),
            self._cfg.asr.language,
            language_card,
        )
        self.language_asr_combo.currentIndexChanged.connect(self._update_engine_hint)
        language_card.add(Field("Язык распознавания", self.language_asr_combo))
        self.language_hint = QLabel("", language_card)
        self.language_hint.setObjectName("FieldHint")
        self.language_hint.setWordWrap(True)
        language_card.add(self.language_hint)
        layout.addWidget(language_card)

        self._on_engine_changed()
        return page

    # --- страница «Текст» --------------------------------------------------
    def _build_text_page(self) -> QWidget:
        page, layout = self._make_page(3)

        processing_card = Card("Обработка текста")
        row, self.punctuation_toggle = self._toggle_row(
            "Пунктуация",
            "Сохранять знаки препинания, которые расставила модель.",
            self._cfg.text.punctuation,
        )
        processing_card.add(row)
        row, self.capitalize_toggle = self._toggle_row(
            "Заглавные буквы",
            "Начинать предложения с большой буквы.",
            self._cfg.text.capitalize,
        )
        processing_card.add(row)
        row, self.remove_fillers_toggle = self._toggle_row(
            "Убирать слова-паразиты",
            "«Ну», «э-э», «как бы» и подобные.",
            self._cfg.text.remove_fillers,
        )
        self.remove_fillers_toggle.toggled.connect(self.filler_words_edit_set_enabled)
        processing_card.add(row)

        self.filler_words_edit = _line(", ".join(self._cfg.text.filler_words), parent=processing_card)
        self.filler_words_field = Field(
            "Слова-паразиты",
            self.filler_words_edit,
            "Через запятую. Удаляются целыми словами, регистр не важен.",
        )
        processing_card.add(self.filler_words_field)
        self.filler_words_field.setEnabled(self._cfg.text.remove_fillers)

        row, self.spoken_toggle = self._toggle_row(
            "Произносимые знаки",
            "«Запятая», «точка», «новая строка» превращаются в знаки препинания.",
            self._cfg.text.spoken_punctuation,
        )
        processing_card.add(row)
        row, self.trailing_space_toggle = self._toggle_row(
            "Пробел в конце",
            "Добавлять пробел после вставленного текста — удобно для чатов.",
            self._cfg.text.trailing_space,
        )
        processing_card.add(row)
        row, self.tidy_spaces_toggle = self._toggle_row(
            "Аккуратные пробелы",
            "Убрать лишние пробелы и пробелы перед знаками препинания.",
            self._cfg.text.tidy_spaces,
        )
        processing_card.add(row)
        layout.addWidget(processing_card)

        dictionary_card = Card(
            "Словарь замен",
            "Исправляйте то, что модель слышит неправильно: имена, термины, аббревиатуры.",
        )
        self.dictionary_table = QTableWidget(0, 3, dictionary_card)
        self.dictionary_table.setHorizontalHeaderLabels(
            ["Как слышится", "Заменять на", "Целое слово"]
        )
        self.dictionary_table.verticalHeader().setVisible(False)
        self.dictionary_table.setAlternatingRowColors(True)
        self.dictionary_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.dictionary_table.setMinimumHeight(210)
        header = self.dictionary_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.dictionary_table.setColumnWidth(2, 120)
        dictionary_card.add(self.dictionary_table)
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        add_button = QPushButton("Добавить", dictionary_card)
        add_button.clicked.connect(self._add_dictionary_row)
        remove_button = QPushButton("Удалить", dictionary_card)
        remove_button.setObjectName("Danger")
        remove_button.clicked.connect(self._remove_dictionary_rows)
        buttons_row.addWidget(add_button)
        buttons_row.addWidget(remove_button)
        buttons_row.addStretch(1)
        hint = QLabel("Замены применяются без учёта регистра.", dictionary_card)
        hint.setObjectName("FieldHint")
        buttons_row.addWidget(hint)
        dictionary_card.add_layout(buttons_row)
        layout.addWidget(dictionary_card)
        self._fill_dictionary(self._cfg.text.dictionary)
        return page

    # --- страница «Полировка LLM» -----------------------------------------
    def _build_polish_page(self) -> QWidget:
        page, layout = self._make_page(4)

        card = Card("Полировка текста нейросетью")
        row, self.polish_toggle = self._toggle_row(
            "Включить полировку",
            "Нейросеть аккуратно расставит пунктуацию и исправит явные ошибки распознавания.",
            self._cfg.polish.enabled,
        )
        self.polish_toggle.toggled.connect(self._on_polish_toggled)
        card.add(row)

        self.polish_fields: list[QWidget] = []
        self.polish_provider_combo = _combo(
            POLISH_PROVIDERS, self._cfg.polish.provider, card
        )
        self.polish_provider_combo.currentIndexChanged.connect(self._on_polish_provider_changed)
        card.add(Field("Сервис", self.polish_provider_combo))
        self.polish_fields.append(self.polish_provider_combo)

        self.polish_url_edit = _line(self._cfg.polish.base_url, "https://api.deepseek.com/v1", card)
        card.add(Field("Адрес API", self.polish_url_edit))
        self.polish_fields.append(self.polish_url_edit)

        self.polish_model_edit = _line(self._cfg.polish.model, "deepseek-chat", card)
        card.add(Field("Модель", self.polish_model_edit))
        self.polish_fields.append(self.polish_model_edit)

        self.polish_env_edit = _line(self._cfg.polish.api_key_env, "DEEPSEEK_API_KEY", card)
        card.add(
            Field(
                "Переменная с API-ключом",
                self.polish_env_edit,
                "Ключ читается из переменной окружения и не сохраняется в конфигурации.",
            )
        )
        self.polish_fields.append(self.polish_env_edit)

        self.polish_style_combo = _combo(STYLE_OPTIONS, self._cfg.polish.style, card)
        card.add(Field("Стиль правки", self.polish_style_combo))
        self.polish_fields.append(self.polish_style_combo)

        self.polish_timeout_spin = _spin(3, 60, int(self._cfg.polish.timeout_s), " с", 1, card)
        card.add(Field("Таймаут запроса", self.polish_timeout_spin))
        self.polish_fields.append(self.polish_timeout_spin)

        self.polish_env_status = QLabel("", card)
        self.polish_env_status.setObjectName("FieldHint")
        self.polish_env_status.setWordWrap(True)
        card.add(self.polish_env_status)
        self.polish_env_edit.textChanged.connect(self._update_polish_env_status)

        privacy = QLabel(
            "Полировка выключена по умолчанию. Если она включена, распознанный текст "
            "отправляется на выбранный сервис; при ошибке или таймауте используется "
            "исходный текст без изменений.",
            card,
        )
        privacy.setObjectName("FieldHint")
        privacy.setWordWrap(True)
        card.add(privacy)
        layout.addWidget(card)

        self._on_polish_toggled(self._cfg.polish.enabled)
        return page

    # --- страница «О программе» -------------------------------------------
    def _build_about_page(self) -> QWidget:
        page, layout = self._make_page(5)

        app_card = Card()
        row = QHBoxLayout()
        row.setSpacing(16)
        logo = QLabel(app_card)
        logo.setPixmap(icons.app_pixmap(72))
        logo.setFixedSize(72, 72)
        row.addWidget(logo)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        title = QLabel("Голос", app_card)
        title.setObjectName("Title")
        texts.addWidget(title)
        texts.addWidget(_faint(f"Офлайн-диктовка для Windows · версия {__version__}", app_card))
        texts.addWidget(
            _faint(
                f"Python {sys.version_info.major}.{sys.version_info.minor} · "
                f"Qt {_qt_version()} · PySide6",
                app_card,
            )
        )
        row.addLayout(texts, 1)
        app_card.add_layout(row)
        layout.addWidget(app_card)

        data_card = Card("Данные и пути")
        self.about_paths: dict[str, QLabel] = {}
        for key, title_text, value in (
            ("config", "Папка конфигурации", paths.display_path(paths.appdata_dir())),
            ("models", "Папка моделей", paths.display_path(paths.models_dir())),
            ("hotkey", "Горячая клавиша", ""),
            ("model", "Активная модель", ""),
        ):
            data_card.add(_path_row(key, title_text, value, self.about_paths, data_card))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        open_config = QPushButton("Открыть папку конфигурации", data_card)
        open_config.clicked.connect(self._open_config_dir)
        open_models = QPushButton("Открыть папку моделей", data_card)
        open_models.clicked.connect(self._open_models_dir)
        copy_info = QPushButton("Скопировать сведения", data_card)
        copy_info.clicked.connect(self._copy_diagnostics)
        buttons.addWidget(open_config)
        buttons.addWidget(open_models)
        buttons.addWidget(copy_info)
        buttons.addStretch(1)
        data_card.add_layout(buttons)
        layout.addWidget(data_card)

        components_card = Card("Компоненты и лицензии")
        components = QLabel(components_card)
        components.setWordWrap(True)
        components.setOpenExternalLinks(True)
        components.setTextFormat(Qt.TextFormat.RichText)
        components.setText(
            '<div style="line-height: 160%;">'
            '<a href="https://github.com/salute-developers/GigaAM">GigaAM-v3</a> — '
            "распознавание русского языка, MIT.<br>"
            '<a href="https://huggingface.co/ai-sage/GigaAM-v3">ai-sage/GigaAM-v3</a> — '
            "веса модели.<br>"
            '<a href="https://www.qt.io/qt-for-python">PySide6 / Qt 6</a> — интерфейс, LGPL v3.<br>'
            '<a href="https://github.com/SYSTRAN/faster-whisper">faster-whisper</a> — '
            "альтернативный офлайн-движок, MIT."
            "</div>"
        )
        components_card.add(components)
        components_card.add(
            _faint(
                "Иконки и звуковые сигналы созданы для этого проекта и входят в поставку.",
                components_card,
            )
        )
        layout.addWidget(components_card)

        privacy_card = Card("Конфиденциальность")
        privacy = QLabel(
            "Распознавание выполняется на компьютере: аудио не отправляется в сеть. "
            "Интернет нужен только для скачивания модели и, если вы включите полировку, "
            "для обращения к выбранному сервису.",
            privacy_card,
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("Muted")
        privacy_card.add(privacy)
        layout.addWidget(privacy_card)
        return page

    # ------------------------------------------------------- загрузка данных
    def _read_config(self) -> AppConfig:
        try:
            return self.controller.get_config()
        except Exception:
            logger.exception("Не удалось прочитать конфигурацию")
            from ..config import default_config

            return default_config()

    def _load_from_config(self) -> None:
        self._loading = True
        cfg = self._cfg
        self.hotkey_edit.set_spec(cfg.hotkey.hotkey, emit=False)
        self.cancel_edit.set_spec(cfg.hotkey.cancel_key, emit=False)
        _select_data(self.mode_combo, cfg.hotkey.mode)
        self.autostart_toggle.setChecked(cfg.ui.autostart)
        self.start_minimized_toggle.setChecked(cfg.ui.start_minimized)
        self.sounds_toggle.setChecked(cfg.ui.sounds)
        self.history_after_insert_toggle.setChecked(cfg.ui.show_history_after_insert)
        self.history_enabled_toggle.setChecked(cfg.history_enabled)
        self.overlay_toggle.setChecked(cfg.ui.overlay_enabled)
        _select_data(self.position_combo, cfg.ui.overlay_position)
        self.position_combo.setEnabled(cfg.ui.overlay_enabled)
        _select_data(self.insert_method_combo, cfg.insert.method)
        self.exclude_history_toggle.setChecked(cfg.insert.exclude_from_history)
        self.restore_clipboard_toggle.setChecked(cfg.insert.restore_clipboard)
        self._on_insert_method_changed()
        _select_data(self.theme_combo, cfg.ui.theme)
        _select_data(self.language_combo, cfg.ui.language)

        self.max_duration_spin.setValue(int(cfg.audio.max_duration_s))
        _select_data(self.vad_combo, cfg.audio.vad)
        _select_data(self.normalize_combo, cfg.audio.normalize)
        self._reload_devices()

        _select_data(self.engine_combo, cfg.asr.engine)
        _select_data(self.gigaam_variant_combo, cfg.asr.gigaam_variant)
        _select_data(self.whisper_model_combo, cfg.asr.whisper_model)
        _select_data(self.compute_combo, cfg.asr.whisper_compute_type)
        self.beam_spin.setValue(int(cfg.asr.whisper_beam_size))
        _select_data(self.cloud_provider_combo, cfg.asr.cloud_provider)
        self.cloud_url_edit.setText(cfg.asr.cloud_base_url)
        self.cloud_model_edit.setText(cfg.asr.cloud_model)
        self.cloud_env_edit.setText(cfg.asr.cloud_api_key_env)
        self.cloud_timeout_spin.setValue(int(cfg.asr.cloud_timeout_s))
        _select_data(self.language_asr_combo, cfg.asr.language)

        self.punctuation_toggle.setChecked(cfg.text.punctuation)
        self.capitalize_toggle.setChecked(cfg.text.capitalize)
        self.remove_fillers_toggle.setChecked(cfg.text.remove_fillers)
        self.filler_words_edit.setText(", ".join(cfg.text.filler_words))
        self.filler_words_field.setEnabled(cfg.text.remove_fillers)
        self.spoken_toggle.setChecked(cfg.text.spoken_punctuation)
        self.trailing_space_toggle.setChecked(cfg.text.trailing_space)
        self.tidy_spaces_toggle.setChecked(cfg.text.tidy_spaces)
        self._fill_dictionary(cfg.text.dictionary)

        self.polish_toggle.setChecked(cfg.polish.enabled)
        _select_data(self.polish_provider_combo, cfg.polish.provider)
        self.polish_url_edit.setText(cfg.polish.base_url)
        self.polish_model_edit.setText(cfg.polish.model)
        self.polish_env_edit.setText(cfg.polish.api_key_env)
        _select_data(self.polish_style_combo, cfg.polish.style)
        self.polish_timeout_spin.setValue(int(cfg.polish.timeout_s))
        self._on_polish_toggled(cfg.polish.enabled)

        self._on_engine_changed()
        self._update_cloud_env_status()
        self._update_polish_env_status()
        self._update_about_values()
        self._loading = False

    def _update_about_values(self) -> None:
        self.about_paths["hotkey"].setText(
            formatting.format_hotkey(self._cfg.hotkey.hotkey)
        )
        self.about_paths["model"].setText(formatting.model_title(self._cfg))

    def _reload_devices(self) -> None:
        current = self.device_combo.currentData() if self.device_combo.count() else None
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("Системный микрофон по умолчанию", "")
        try:
            devices = self.controller.get_devices() or []
        except Exception as exc:
            logger.warning("Не удалось получить список микрофонов: %s", exc)
            devices = []
        for device in devices:
            label = device.name
            if getattr(device, "is_default", False):
                label += " (по умолчанию)"
            self.device_combo.addItem(label, device.name)
        target = current if current is not None else (self._cfg.audio.input_device or "")
        index = self.device_combo.findData(target)
        self.device_combo.setCurrentIndex(index if index >= 0 else 0)
        self.device_combo.blockSignals(False)

    def _fill_dictionary(self, entries) -> None:
        self.dictionary_table.setRowCount(0)
        for entry in entries:
            self._append_dictionary_row(entry.source, entry.target, entry.whole_word)

    def _append_dictionary_row(self, source: str, target: str, whole_word: bool = True) -> None:
        row = self.dictionary_table.rowCount()
        self.dictionary_table.insertRow(row)
        self.dictionary_table.setItem(row, 0, QTableWidgetItem(source))
        self.dictionary_table.setItem(row, 1, QTableWidgetItem(target))
        item = QTableWidgetItem("")
        item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        item.setCheckState(Qt.CheckState.Checked if whole_word else Qt.CheckState.Unchecked)
        self.dictionary_table.setItem(row, 2, item)
        self.dictionary_table.setRowHeight(row, 36)

    def _add_dictionary_row(self) -> None:
        self._append_dictionary_row("", "", True)
        row = self.dictionary_table.rowCount() - 1
        self.dictionary_table.scrollToBottom()
        self.dictionary_table.editItem(self.dictionary_table.item(row, 0))

    def _remove_dictionary_rows(self) -> None:
        rows = sorted({index.row() for index in self.dictionary_table.selectedIndexes()}, reverse=True)
        if not rows and self.dictionary_table.rowCount():
            rows = [self.dictionary_table.rowCount() - 1]
        for row in rows:
            self.dictionary_table.removeRow(row)

    def _collect_dictionary(self) -> list[DictionaryEntry]:
        entries: list[DictionaryEntry] = []
        for row in range(self.dictionary_table.rowCount()):
            source_item = self.dictionary_table.item(row, 0)
            target_item = self.dictionary_table.item(row, 1)
            source = (source_item.text() if source_item else "").strip()
            target = (target_item.text() if target_item else "").strip()
            if not source:
                continue
            check = self.dictionary_table.item(row, 2)
            whole_word = (
                check is None or check.checkState() == Qt.CheckState.Checked
            )
            entries.append(DictionaryEntry(source=source, target=target, whole_word=whole_word))
        return entries

    # ----------------------------------------------------- обработчики формы
    def _on_nav_changed(self, row: int) -> None:
        if row < 0 or not hasattr(self, "stack"):
            return
        self.stack.setCurrentIndex(row)
        if row == 2:
            self._refresh_engine_status()

    def _on_theme_changed(self, _index: int) -> None:
        if self._loading:
            return
        self.theme_preview_requested.emit(self.theme_combo.currentData())

    def _on_insert_method_changed(self, *_args) -> None:
        """В режиме прямого ввода буфер не участвует — связанные опции неактивны."""
        uses_clipboard = (self.insert_method_combo.currentData() or "clipboard") == "clipboard"
        self.exclude_history_toggle.setEnabled(uses_clipboard)
        self.restore_clipboard_toggle.setEnabled(uses_clipboard)

    def _on_engine_changed(self, *_args) -> None:
        engine = self.engine_combo.currentData()
        self.gigaam_card.setVisible(engine == "gigaam")
        self.whisper_card.setVisible(engine == "whisper")
        self.cloud_card.setVisible(engine == "cloud")
        self._update_engine_hint()
        self._update_cloud_env_status()

    def _update_engine_hint(self) -> None:
        engine = self.engine_combo.currentData()
        if engine == "gigaam":
            self.engine_hint.setText(
                "Локальная модель GigaAM-v3: работает офлайн на CPU, аудио не покидает компьютер."
            )
        elif engine == "whisper":
            self.engine_hint.setText(
                "faster-whisper работает офлайн. Модели скачиваются один раз и кешируются."
            )
        else:
            self.engine_hint.setText(
                "Облачное распознавание отправляет аудио на сервер по HTTPS. "
                "Нужен API-ключ в переменной окружения."
            )
        language = self.language_asr_combo.currentData()
        if engine == "gigaam":
            self.language_hint.setText(
                "GigaAM-v3 — русскоязычная модель, язык определяется автоматически и не настраивается."
            )
            self.language_asr_combo.setEnabled(False)
        else:
            self.language_hint.setText(
                "Автоопределение удобно для смешанной речи; для диктовки на русском "
                "лучше явно указать язык."
            )
            self.language_asr_combo.setEnabled(True)
        _ = language

    def _on_polish_toggled(self, enabled: bool) -> None:
        for widget in getattr(self, "polish_fields", []):
            widget.setEnabled(enabled)
        if hasattr(self, "polish_env_status"):
            self.polish_env_status.setEnabled(enabled)

    def filler_words_edit_set_enabled(self, enabled: bool) -> None:
        self.filler_words_field.setEnabled(enabled)

    def _on_polish_provider_changed(self, _index: int) -> None:
        if self._loading:
            return
        provider = self.polish_provider_combo.currentData()
        preset = {
            "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
            "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
            "custom": (None, None, None),
        }.get(provider)
        if not preset:
            return
        url, model, env = preset
        if url:
            self.polish_url_edit.setText(url)
        if model:
            self.polish_model_edit.setText(model)
        if env:
            self.polish_env_edit.setText(env)

    def _on_cloud_provider_changed(self, _index: int) -> None:
        if self._loading:
            return
        provider = self.cloud_provider_combo.currentData()
        preset = {
            "openai": ("https://api.openai.com/v1", "gpt-4o-transcribe", "OPENAI_API_KEY"),
            "groq": ("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "GROQ_API_KEY"),
            "custom": (None, None, None),
        }.get(provider)
        if not preset:
            return
        url, model, env = preset
        if url:
            self.cloud_url_edit.setText(url)
            self.cloud_model_edit.setText(model)
            self.cloud_env_edit.setText(env)

    def _update_cloud_env_status(self) -> None:
        env_name = self.cloud_env_edit.text().strip()
        is_set = bool(env_name) and bool(os.environ.get(env_name))
        self.cloud_env_status.setText(formatting.env_status_text(env_name, is_set))
        self.cloud_env_status.setStyleSheet(
            f"color: {theme.current().success if is_set else theme.current().faint};"
        )

    def _update_polish_env_status(self) -> None:
        env_name = self.polish_env_edit.text().strip()
        is_set = bool(env_name) and bool(os.environ.get(env_name))
        self.polish_env_status.setText(formatting.env_status_text(env_name, is_set))
        self.polish_env_status.setStyleSheet(
            f"color: {theme.current().success if is_set else theme.current().faint};"
        )

    # --- модель ------------------------------------------------------------
    def _refresh_engine_status(self) -> None:
        if not hasattr(self, "asr_status"):
            return
        try:
            status = self.controller.engine_status() or {}
        except Exception as exc:
            logger.warning("Не удалось получить статус модели: %s", exc)
            return
        text = formatting.engine_status_text(status)
        self.asr_status.setText(text)
        if status.get("loading"):
            self.asr_status.setStyleSheet(f"color: {theme.current().accent};")
            self.load_button.setText("Загружается…")
            self.load_button.setEnabled(False)
        elif status.get("loaded"):
            self.asr_status.setStyleSheet(f"color: {theme.current().success};")
            self.load_button.setText("Проверить модель")
            self.load_button.setEnabled(True)
        else:
            self.asr_status.setStyleSheet(f"color: {theme.current().muted};")
            self.load_button.setText("Скачать и загрузить модель")
            self.load_button.setEnabled(True)
        if not status.get("loading"):
            self.asr_progress.setVisible(False)
        self._update_about_values()

    def _load_model(self) -> None:
        self.set_status_message("Сохраняем настройки и запускаем загрузку модели…", "muted")
        if not self._apply_config(close=False):
            return
        try:
            self.controller.load_engine()
        except Exception as exc:
            logger.exception("Не удалось запустить загрузку модели")
            self.set_status_message(f"Не удалось загрузить модель: {exc}", "danger")
        self._refresh_engine_status()

    def _test_microphone(self) -> None:
        try:
            result = self.controller.test_microphone()
        except AttributeError:
            self.mic_test_status.setText(
                "Проверка микрофона не поддерживается подключённым ядром."
            )
            return
        except Exception as exc:
            logger.exception("Ошибка проверки микрофона")
            self.mic_test_status.setText(f"Не удалось проверить микрофон: {exc}")
            return
        if result is False:
            self.mic_test_status.setText("Проверка уже идёт — дождитесь её завершения.")
            return
        self._test_active = True
        self.test_button.setEnabled(False)
        self.test_button.setText("Проверяем…")
        self.mic_test_status.setText("Говорите в микрофон: индикатор покажет уровень сигнала.")
        self.mic_meter.set_active(True)
        QTimer.singleShot(4000, self._finish_microphone_test)

    def _finish_microphone_test(self) -> None:
        self._test_active = False
        self.test_button.setEnabled(True)
        self.test_button.setText("Проверить микрофон")
        self.mic_test_status.setText("Проверка завершена. Если индикатор двигался — микрофон работает.")
        self.mic_meter.set_active(False)

    # --- сервис ------------------------------------------------------------
    def _open_config_dir(self) -> None:
        try:
            self.controller.open_config_dir()
        except Exception as exc:
            logger.warning("Не удалось открыть папку конфигурации: %s", exc)
            QDesktopServices.openUrl(paths.appdata_dir().as_uri())

    def _open_models_dir(self) -> None:
        QDesktopServices.openUrl(paths.models_dir().as_uri())

    def _copy_diagnostics(self) -> None:
        lines = [
            f"Голос {__version__}",
            f"Python {platform.python_version()}",
            f"Qt {_qt_version()}",
            f"ОС: {platform.platform()}",
            f"Модель: {formatting.model_title(self._collect())}",
            f"Горячая клавиша: {formatting.format_hotkey(self.hotkey_edit.spec())}",
            f"Папка конфигурации: {paths.display_path(paths.appdata_dir())}",
            f"Папка моделей: {paths.display_path(paths.models_dir())}",
        ]
        text = paths.sanitize_for_report("\n".join(lines))
        try:
            self.controller.copy_to_clipboard(text)
        except Exception:
            copy_text(text)
        self.set_status_message("Сведения о системе скопированы в буфер обмена", "success")

    # --- сохранение --------------------------------------------------------
    def _collect(self) -> AppConfig:
        cfg = copy.deepcopy(self._original)
        cfg.hotkey.hotkey = self.hotkey_edit.spec()
        cfg.hotkey.mode = self.mode_combo.currentData() or "toggle"
        cfg.hotkey.cancel_key = self.cancel_edit.spec() or "esc"
        cfg.ui.autostart = self.autostart_toggle.isChecked()
        cfg.ui.start_minimized = self.start_minimized_toggle.isChecked()
        cfg.ui.sounds = self.sounds_toggle.isChecked()
        cfg.ui.show_history_after_insert = self.history_after_insert_toggle.isChecked()
        cfg.history_enabled = self.history_enabled_toggle.isChecked()
        cfg.ui.overlay_enabled = self.overlay_toggle.isChecked()
        cfg.ui.overlay_position = self.position_combo.currentData() or "bottom_center"
        cfg.ui.theme = self.theme_combo.currentData() or "dark"
        cfg.ui.language = self.language_combo.currentData() or "ru"

        cfg.insert.method = self.insert_method_combo.currentData() or "clipboard"
        cfg.insert.exclude_from_history = self.exclude_history_toggle.isChecked()
        cfg.insert.restore_clipboard = self.restore_clipboard_toggle.isChecked()

        cfg.audio.input_device = self.device_combo.currentData() or None
        cfg.audio.max_duration_s = float(self.max_duration_spin.value())
        cfg.audio.vad = self.vad_combo.currentData() or "silero"
        cfg.audio.normalize = self.normalize_combo.currentData() or "peak"

        cfg.asr.engine = self.engine_combo.currentData() or "gigaam"
        cfg.asr.gigaam_variant = self.gigaam_variant_combo.currentData() or "e2e_rnnt"
        cfg.asr.whisper_model = self.whisper_model_combo.currentData() or "large-v3-turbo"
        cfg.asr.whisper_compute_type = self.compute_combo.currentData() or "int8"
        cfg.asr.whisper_beam_size = int(self.beam_spin.value())
        cfg.asr.cloud_provider = self.cloud_provider_combo.currentData() or "openai"
        cfg.asr.cloud_base_url = self.cloud_url_edit.text().strip()
        cfg.asr.cloud_model = self.cloud_model_edit.text().strip()
        cfg.asr.cloud_api_key_env = self.cloud_env_edit.text().strip()
        cfg.asr.cloud_timeout_s = float(self.cloud_timeout_spin.value())
        cfg.asr.language = self.language_asr_combo.currentData() or "ru"

        cfg.text.punctuation = self.punctuation_toggle.isChecked()
        cfg.text.capitalize = self.capitalize_toggle.isChecked()
        cfg.text.remove_fillers = self.remove_fillers_toggle.isChecked()
        cfg.text.filler_words = [
            word.strip()
            for word in self.filler_words_edit.text().replace(";", ",").split(",")
            if word.strip()
        ]
        cfg.text.spoken_punctuation = self.spoken_toggle.isChecked()
        cfg.text.trailing_space = self.trailing_space_toggle.isChecked()
        cfg.text.tidy_spaces = self.tidy_spaces_toggle.isChecked()
        cfg.text.dictionary = self._collect_dictionary()

        cfg.polish.enabled = self.polish_toggle.isChecked()
        cfg.polish.provider = self.polish_provider_combo.currentData() or "deepseek"
        cfg.polish.base_url = self.polish_url_edit.text().strip()
        cfg.polish.model = self.polish_model_edit.text().strip()
        cfg.polish.api_key_env = self.polish_env_edit.text().strip()
        cfg.polish.style = self.polish_style_combo.currentData() or "dictation"
        cfg.polish.timeout_s = float(self.polish_timeout_spin.value())
        return cfg

    def _validate(self, cfg: AppConfig) -> str | None:
        error = formatting.validate_hotkey_spec(cfg.hotkey.hotkey)
        if error:
            return error
        cancel = formatting.validate_cancel_key(cfg.hotkey.cancel_key)
        if cancel:
            return cancel
        if cfg.hotkey.cancel_key == cfg.hotkey.hotkey:
            return "Клавиша отмены совпадает с горячей клавишей — выберите другую."
        if cfg.asr.engine == "cloud":
            if not cfg.asr.cloud_base_url:
                return "Укажите адрес API облачного распознавания."
            if not cfg.asr.cloud_model:
                return "Укажите модель облачного распознавания."
            if not cfg.asr.cloud_api_key_env:
                return "Укажите имя переменной окружения с API-ключом."
        if cfg.polish.enabled:
            if not cfg.polish.base_url:
                return "Укажите адрес API для полировки текста."
            if not cfg.polish.model:
                return "Укажите модель для полировки текста."
        return None

    def _apply_config(self, close: bool) -> bool:
        cfg = self._collect()
        error = self._validate(cfg)
        if error:
            self.set_status_message(error, "danger")
            return False
        try:
            self.controller.apply_config(cfg, save=True)
        except Exception as exc:
            logger.exception("Не удалось применить настройки")
            self.set_status_message(f"Не удалось сохранить настройки: {exc}", "danger")
            return False
        self._cfg = cfg
        self._original = copy.deepcopy(cfg)
        self._update_about_values()
        if cfg.asr.engine == "cloud" and not os.environ.get(cfg.asr.cloud_api_key_env):
            self.set_status_message(
                "Настройки сохранены. Внимание: API-ключ для облачного распознавания не найден.",
                "warning",
            )
        else:
            self.set_status_message("Настройки сохранены", "success")
        self.saved.emit(cfg)
        if close:
            self.accept()
        return True

    def _save(self) -> None:
        self._apply_config(close=True)

    # --- события окна ------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._loading:
            self.refresh()
        self._status_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._status_timer.stop()
        super().hideEvent(event)


# --------------------------------------------------------------------------- #
# Вспомогательные функции страниц
# --------------------------------------------------------------------------- #
def _faint(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label


def _path_row(
    key: str,
    title: str,
    value: str,
    storage: dict,
    parent: QWidget,
) -> QWidget:
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    title_label = QLabel(title, row)
    title_label.setObjectName("FieldLabel")
    layout.addWidget(title_label)
    value_label = QLabel(value, row)
    value_label.setObjectName("Faint")
    value_label.setWordWrap(True)
    value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(value_label)
    storage[key] = value_label
    return row


def _select_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion

        return qVersion()
    except Exception:  # pragma: no cover
        return "6"
