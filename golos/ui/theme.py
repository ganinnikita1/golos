"""Единое оформление приложения «Голос».

Тёмная тема по умолчанию, спокойная палитра (тёмно-серый фон, мягкий
бирюзовый акцент), типографика Segoe UI, радиусы 8-12 px, отступы 8/16.
Вся визуальная система собрана здесь: палитры, QSS и помощники для
кастомных виджетов, которые рисуют себя сами.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from .. import paths

#: Основной шрифт интерфейса (есть в любой Windows 10/11).
FONT_FAMILY = "Segoe UI"
#: Моноширинный — для таймера, горячих клавиш и RTF.
MONO_FAMILY = "Cascadia Mono, Consolas"

#: Шаг сетки отступов: 8 / 16 / 24.
SPACE = 8
GAP = 16
MARGIN = 24
RADIUS = 10
RADIUS_CARD = 12
RADIUS_BUTTON = 8


@dataclass(frozen=True)
class Palette:
    """Набор цветов одной темы."""

    name: str
    bg: str
    bg_alt: str
    surface: str
    surface_alt: str
    surface_hover: str
    elevated: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    danger: str
    danger_soft: str
    warning: str
    success: str
    overlay_top: str
    overlay_bottom: str
    overlay_border: str
    overlay_text: str
    overlay_muted: str
    scrollbar: str
    selection: str


DARK = Palette(
    name="dark",
    bg="#15171C",
    bg_alt="#111317",
    surface="#1C1F26",
    surface_alt="#242830",
    surface_hover="#2B303A",
    elevated="#262B34",
    border="#2B3038",
    border_strong="#3A4150",
    text="#E9ECF1",
    muted="#9CA6B4",
    faint="#6C7686",
    accent="#4FD1C5",
    accent_hover="#6FE0D4",
    accent_pressed="#3BB9AE",
    accent_soft="rgba(79, 209, 197, 0.14)",
    on_accent="#07211E",
    danger="#F0736B",
    danger_soft="rgba(240, 115, 107, 0.14)",
    warning="#E8B65A",
    success="#6FCF97",
    overlay_top="#2A303A",
    overlay_bottom="#1A1E25",
    overlay_border="rgba(255, 255, 255, 0.10)",
    overlay_text="#F3F6F9",
    overlay_muted="#A8B3C1",
    scrollbar="#3A4150",
    selection="rgba(79, 209, 197, 0.28)",
)

LIGHT = Palette(
    name="light",
    bg="#F3F5F8",
    bg_alt="#EAEEF3",
    surface="#FFFFFF",
    surface_alt="#F1F4F7",
    surface_hover="#E8EDF2",
    elevated="#FFFFFF",
    border="#DDE3EA",
    border_strong="#C3CCD7",
    text="#1A1F26",
    muted="#5E6875",
    faint="#8A94A2",
    accent="#0F9D92",
    accent_hover="#0C8A80",
    accent_pressed="#0A756D",
    accent_soft="rgba(15, 157, 146, 0.12)",
    on_accent="#FFFFFF",
    danger="#D9534F",
    danger_soft="rgba(217, 83, 79, 0.10)",
    warning="#B7791F",
    success="#2F9E62",
    overlay_top="#FFFFFF",
    overlay_bottom="#F2F5F8",
    overlay_border="rgba(15, 23, 42, 0.10)",
    overlay_text="#16202A",
    overlay_muted="#5E6875",
    scrollbar="#C3CCD7",
    selection="rgba(15, 157, 146, 0.20)",
)

_THEMES = {"dark": DARK, "light": LIGHT}
_current: Palette = DARK


def current() -> Palette:
    """Активная палитра (по умолчанию тёмная)."""
    return _current


def get_palette(name: str) -> Palette:
    return _THEMES.get(name, DARK)


def resolve_theme(name: str | None) -> str:
    """Приводит 'system' к конкретной теме по настройкам Windows."""
    name = (name or "dark").lower()
    if name in _THEMES:
        return name
    return "light" if system_is_light() else "dark"


def system_is_light() -> bool:
    """Определяет светлую тему ОС; при любой ошибке — тёмная."""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return True
        if scheme == Qt.ColorScheme.Dark:
            return False
    except Exception:  # pragma: no cover - зависит от платформы
        pass
    return False


def qcolor(value: str, alpha: int | None = None) -> QColor:
    """QColor из '#RRGGBB' или 'rgba(r, g, b, a)'."""
    value = value.strip()
    if value.startswith("rgba("):
        try:
            inner = value[5:-1].replace(" ", "")
            r, g, b, a = (float(part) for part in inner.split(","))
            color = QColor(int(r), int(g), int(b), int(round(a * 255)))
            if alpha is not None:
                color.setAlpha(alpha)
            return color
        except (ValueError, TypeError):
            return QColor(0, 0, 0)
    color = QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
_QSS = Template(
    """
/* ---------- база ---------- */
QWidget {
    color: $text;
    font-family: "$font";
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#RootWindow {
    background-color: $bg;
}
QWidget#Sidebar {
    background-color: $bg_alt;
    border-right: 1px solid $border;
}
QWidget#FooterBar {
    background-color: $surface;
    border-top: 1px solid $border;
}
QLabel {
    background: transparent;
}
QLabel#Title {
    font-size: 22px;
    font-weight: 600;
    color: $text;
}
QLabel#Heading {
    font-size: 17px;
    font-weight: 600;
    color: $text;
}
QLabel#Subtitle {
    font-size: 13px;
    color: $muted;
}
QLabel#SectionTitle {
    font-size: 11px;
    font-weight: 600;
    color: $muted;
}
QLabel#CardTitle {
    font-size: 15px;
    font-weight: 600;
    color: $text;
}
QLabel#FieldLabel {
    font-size: 12px;
    font-weight: 600;
    color: $muted;
}
QLabel#FieldHint {
    font-size: 12px;
    color: $faint;
}
QLabel#Stat {
    font-size: 12px;
    color: $muted;
}
QLabel#Muted {
    color: $muted;
}
QLabel#Faint {
    color: $faint;
    font-size: 12px;
}
QLabel#Accent {
    color: $accent;
}
QLabel#Danger {
    color: $danger;
}
QLabel#Success {
    color: $success;
}
QLabel#Warning {
    color: $warning;
}
QLabel#Timer {
    font-family: $mono;
    font-size: 20px;
    font-weight: 600;
    color: $text;
}
QLabel#Value {
    font-family: $mono;
    font-size: 13px;
    color: $text;
}
QLabel#Link, QLabel#Link a {
    color: $accent;
}

/* ---------- карточки и разделители ---------- */
QFrame#Card {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: $radius_card;
}
QFrame#CardFlat {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: $radius;
}
QFrame#Separator {
    background-color: $border;
    border: none;
    max-height: 1px;
}
QFrame#Footer {
    background-color: $surface;
    border-top: 1px solid $border;
}

/* ---------- кнопки ---------- */
QPushButton {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: $radius_button;
    padding: 7px 14px;
    min-height: 20px;
    color: $text;
    font-weight: 500;
}
QPushButton:hover {
    background-color: $surface_hover;
    border-color: $border_strong;
}
QPushButton:pressed {
    background-color: $surface;
}
QPushButton:focus {
    border-color: $accent;
}
QPushButton:disabled {
    color: $faint;
    background-color: $surface;
    border-color: $border;
}
QPushButton#Primary {
    background-color: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 600;
}
QPushButton#Primary:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QPushButton#Primary:pressed {
    background-color: $accent_pressed;
    border-color: $accent_pressed;
}
QPushButton#Primary:disabled {
    background-color: $surface_alt;
    border-color: $border;
    color: $faint;
}
QPushButton#Ghost {
    background-color: transparent;
    border: 1px solid transparent;
    color: $muted;
}
QPushButton#Ghost:hover {
    background-color: $surface_alt;
    color: $text;
}
QPushButton#Ghost:pressed {
    background-color: $surface;
}
QPushButton#Danger {
    background-color: transparent;
    border: 1px solid $border;
    color: $danger;
}
QPushButton#Danger:hover {
    background-color: $danger_soft;
    border-color: $danger;
}
QPushButton#Link {
    background-color: transparent;
    border: none;
    color: $accent;
    padding: 2px 4px;
    font-weight: 500;
}
QPushButton#Link:hover {
    color: $accent_hover;
}
QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: $radius_button;
    padding: 5px;
    color: $muted;
}
QToolButton:hover {
    background-color: $surface_alt;
    color: $text;
}

/* ---------- поля ввода ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: $radius_button;
    padding: 6px 10px;
    min-height: 20px;
    color: $text;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: $border_strong;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: $accent;
    background-color: $surface;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: $faint;
    background-color: $surface;
}
QLineEdit#HotkeyField {
    font-family: $mono;
    font-size: 13px;
    font-weight: 600;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: url($assets/ui/chevron_down_$theme.png);
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background-color: $elevated;
    border: 1px solid $border;
    border-radius: $radius_button;
    padding: 4px;
    outline: 0;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QComboBox QAbstractItemView::item {
    min-height: 26px;
    padding: 2px 8px;
    border-radius: 6px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px;
    border: none;
}
QPlainTextEdit#DiagView {
    font-family: $mono;
    font-size: 12px;
}

/* ---------- чекбоксы и переключатели ---------- */
QCheckBox {
    spacing: 8px;
    color: $text;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid $border_strong;
    background-color: $surface_alt;
}
QCheckBox::indicator:hover {
    border-color: $accent;
}
QCheckBox::indicator:checked {
    background-color: $accent;
    border-color: $accent;
    image: url($assets/ui/check_$theme.png);
}

/* ---------- список-навигация ---------- */
QListWidget#Nav {
    background-color: transparent;
    border: none;
    padding: 2px;
    outline: 0;
}
QListWidget#Nav::item {
    color: $muted;
    padding: 9px 12px;
    margin: 1px 0px;
    border-radius: $radius_button;
    border-left: 2px solid transparent;
}
QListWidget#Nav::item:hover {
    background-color: $surface_alt;
    color: $text;
}
QListWidget#Nav::item:selected {
    background-color: $accent_soft;
    color: $accent;
    border-left: 2px solid $accent;
    font-weight: 600;
}

/* ---------- таблицы ---------- */
QTableWidget {
    background-color: transparent;
    border: none;
    gridline-color: transparent;
    alternate-background-color: $surface_alt;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QTableWidget::item {
    padding: 6px 8px;
    border-bottom: 1px solid $border;
}
QTableWidget::item:selected {
    background-color: $accent_soft;
    color: $text;
}
QHeaderView::section {
    background-color: transparent;
    color: $muted;
    font-size: 11px;
    font-weight: 600;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid $border;
}
QTableCornerButton::section {
    background-color: transparent;
    border: none;
}

/* ---------- прокрутка ---------- */
QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $scrollbar;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: $border_strong;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $scrollbar;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ---------- прогресс ---------- */
QProgressBar {
    background-color: $surface_alt;
    border: none;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 4px;
}

/* ---------- меню, подсказки ---------- */
QMenu {
    background-color: $elevated;
    border: 1px solid $border;
    border-radius: $radius;
    padding: 6px;
}
QMenu::item {
    padding: 8px 28px 8px 32px;
    border-radius: 6px;
    color: $text;
}
QMenu::item:selected {
    background-color: $accent_soft;
    color: $text;
}
QMenu::item:disabled {
    color: $faint;
}
QMenu::indicator {
    width: 16px;
    height: 16px;
    margin-left: 10px;
}
QMenu::indicator:unchecked {
    image: url($assets/ui/menu_box_$theme.png);
}
QMenu::indicator:checked {
    image: url($assets/ui/menu_check_$theme.png);
}
QMenu::separator {
    height: 1px;
    background-color: $border;
    margin: 5px 8px;
}
QMenu::icon {
    padding-left: 8px;
}
QToolTip {
    background-color: $elevated;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 6px 8px;
}

/* ---------- слайдер (демо) ---------- */
QSlider::groove:horizontal {
    height: 6px;
    background: $surface_alt;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0px;
    border-radius: 8px;
    background: $text;
    border: 2px solid $bg;
}
QSlider::handle:horizontal:hover {
    background: $accent;
}

/* ---------- сообщения ---------- */
QMessageBox {
    background-color: $bg;
}
QMessageBox QLabel {
    color: $text;
}
QMessageBox QPushButton {
    min-width: 88px;
}
"""
)


def build_qss(palette: Palette | None = None) -> str:
    """Собирает стилевой лист для палитры (по умолчанию — активной)."""
    palette = palette or _current
    assets = paths.assets_dir().as_posix()
    return _QSS.safe_substitute(
        font=FONT_FAMILY,
        mono=MONO_FAMILY,
        theme=palette.name,
        assets=assets,
        radius=RADIUS,
        radius_card=RADIUS_CARD,
        radius_button=RADIUS_BUTTON,
        **palette.__dict__,
    )


def build_palette(palette: Palette | None = None) -> QPalette:
    """QPalette, чтобы нативные элементы (диалоги, скроллы) не выбивались."""
    palette = palette or _current
    pal = QPalette()
    window = qcolor(palette.bg)
    text = qcolor(palette.text)
    base = qcolor(palette.surface_alt)
    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, qcolor(palette.surface))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, qcolor(palette.surface))
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, qcolor(palette.danger))
    pal.setColor(QPalette.ColorRole.Highlight, qcolor(palette.accent))
    pal.setColor(QPalette.ColorRole.HighlightedText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, qcolor(palette.elevated))
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, qcolor(palette.faint))
    pal.setColor(QPalette.ColorRole.Link, qcolor(palette.accent))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, qcolor(palette.faint))
    return pal


def apply(app: QApplication, theme: str | None = None) -> Palette:
    """Применяет тему ко всему приложению и возвращает активную палитру."""
    global _current
    name = resolve_theme(theme)
    palette = get_palette(name)
    _current = palette
    try:
        app.setStyle("Fusion")
    except Exception:  # pragma: no cover - стиль может отсутствовать
        pass
    app.setPalette(build_palette(palette))
    app.setStyleSheet(build_qss(palette))
    font = QFont(FONT_FAMILY)
    font.setPixelSize(13)
    app.setFont(font)
    # Кастомные виджеты перерисовывают себя из палитры — просим обновиться.
    for widget in app.allWidgets():
        try:
            widget.update()
        except Exception:  # pragma: no cover
            pass
    return palette


def app_icon_path(kind: str = "app") -> Path:
    """Путь к PNG-иконке приложения/трея; kind: app | idle | recording | busy | error."""
    folder = "app" if kind == "app" else "tray"
    name = "golos_256.png" if kind == "app" else f"{kind}_48.png"
    return paths.assets_dir() / folder / name
