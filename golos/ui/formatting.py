"""Форматирование значений для интерфейса: клавиши, время, числа, подписи.

Отдельный модуль без Qt-зависимостей (кроме необязательного QKeySequence в
помощнике для редактора горячих клавиш), чтобы его было легко тестировать.
"""

from __future__ import annotations

import math
from datetime import datetime

#: Отображаемые названия движков распознавания.
ENGINE_LABELS = {
    "gigaam": "GigaAM-v3",
    "whisper": "faster-whisper",
    "cloud": "Облачное распознавание",
}

#: Человеческие названия горячих клавиш (нижний регистр спецификации → подпись).
_KEY_LABELS = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "meta": "Win",
    "cmd": "Win",
    "esc": "Esc",
    "escape": "Esc",
    "space": "Space",
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "printscreen": "PrintScreen",
    "capslock": "CapsLock",
    "pause": "Pause",
    "menu": "Menu",
    "apps": "Menu",
    "numlock": "NumLock",
}

#: Обратное соответствие для редактора сочетаний (Qt-токен → спецификация).
_QT_TOKEN_TO_SPEC = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "meta": "win",
    "win": "win",
    "esc": "esc",
    "escape": "esc",
    "space": "space",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "printscreen": "printscreen",
    "capslock": "capslock",
    "pause": "pause",
    "menu": "menu",
}

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


# --------------------------------------------------------------------------- #
# Горячие клавиши
# --------------------------------------------------------------------------- #
def format_hotkey(spec: str) -> str:
    """'ctrl+alt+space' → 'Ctrl+Alt+Space'; пустая строка → 'Не задана'."""
    parts = [part.strip() for part in (spec or "").split("+") if part.strip()]
    if not parts:
        return "Не задана"
    return "+".join(_format_key_token(part) for part in parts)


def _format_key_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    # «right alt» / «left ctrl»: сохраняем сторону, но нормализуем регистр.
    words = token.split()
    if len(words) == 2 and words[0].lower() in {"left", "right"}:
        side = "Left" if words[0].lower() == "left" else "Right"
        return f"{side} {_format_key_token(words[1])}"
    low = token.lower()
    if low in _KEY_LABELS:
        return _KEY_LABELS[low]
    if len(token) == 1:
        return token.upper()
    if low.startswith("f") and low[1:].isdigit():
        return f"F{low[1:]}"
    return token[:1].upper() + token[1:]


def hotkey_spec_to_qt(spec: str) -> str:
    """Спецификация → строка для QKeySequence ('Ctrl+Alt+Space')."""
    return format_hotkey(spec)


def key_sequence_to_spec(sequence) -> str:
    """QKeySequence → спецификация в стиле конфига ('ctrl+alt+space')."""
    text = sequence.toString(sequence.SequenceFormat.PortableText)
    if not text:
        return ""
    tokens: list[str] = []
    for raw in text.split("+"):
        low = raw.strip().lower()
        if not low:
            continue
        mapped = _QT_TOKEN_TO_SPEC.get(low)
        if mapped is not None:
            tokens.append(mapped)
            continue
        if len(low) == 1:
            tokens.append(low)
        elif low.startswith("f") and low[1:].isdigit():
            tokens.append(low)
        else:
            tokens.append(low)
    return "+".join(tokens)


def validate_hotkey_spec(spec: str) -> str | None:
    """Возвращает текст ошибки или None, если сочетание допустимо.

    Глобальный хук перехватывает клавишу у всех приложений, поэтому одиночная
    буква/цифра без модификатора — почти всегда ошибка пользователя.
    """
    parts = [part.strip().lower() for part in (spec or "").split("+") if part.strip()]
    if not parts:
        return "Задайте сочетание клавиш — без него диктовку не вызвать."
    function_key = any(part.startswith("f") and part[1:].isdigit() for part in parts)
    has_modifier = any(part in {"ctrl", "alt", "shift", "win"} for part in parts)
    if not has_modifier and not function_key:
        return "Добавьте модификатор (Ctrl, Alt или Shift): одиночная клавиша перехватит ввод."
    if len(parts) == 1 and not function_key:
        return "Укажите хотя бы одну клавишу вместе с модификатором."
    return None


def validate_cancel_key(spec: str) -> str | None:
    """Клавиша отмены должна быть одиночной и не совпадать с основной."""
    parts = [part.strip().lower() for part in (spec or "").split("+") if part.strip()]
    if not parts:
        return "Задайте клавишу отмены записи."
    if len(parts) > 1:
        return "Клавиша отмены должна быть одиночной, без модификаторов."
    return None


# --------------------------------------------------------------------------- #
# Время и длительность
# --------------------------------------------------------------------------- #
def format_elapsed(seconds: float) -> str:
    """Таймер записи: '0:05', '12:34', '1:02:03'."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """Человеческая длительность: '0,8 с', '12 с', '1 мин 05 с'."""
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 1:
        return f"{seconds:.1f}".replace(".", ",") + " с"
    if seconds < 60:
        return f"{int(round(seconds))} с"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes} мин {secs:02d} с"


def format_rtf(value: float) -> str:
    """RTF в русской записи: '0,12'."""
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def format_clock(timestamp: float) -> str:
    """Время записи: '14:32'."""
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def format_timestamp(timestamp: float) -> str:
    """Дата записи: 'Сегодня, 14:32', 'Вчера, 09:05', '3 ноября, 14:32'."""
    try:
        moment = datetime.fromtimestamp(float(timestamp))
    except (TypeError, ValueError, OSError):
        return "—"
    now = datetime.now()
    if moment.date() == now.date():
        return f"Сегодня, {moment:%H:%M}"
    delta_days = (now.date() - moment.date()).days
    if delta_days == 1:
        return f"Вчера, {moment:%H:%M}"
    if now.year == moment.year:
        return f"{moment.day} {_MONTHS_GENITIVE[moment.month - 1]}, {moment:%H:%M}"
    return f"{moment.day} {_MONTHS_GENITIVE[moment.month - 1]} {moment.year}, {moment:%H:%M}"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русская форма числительного: 1 запись, 2 записи, 5 записей."""
    try:
        number = abs(int(count))
    except (TypeError, ValueError):
        number = 0
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def records_label(count: int) -> str:
    return f"{count} {plural(count, 'запись', 'записи', 'записей')}"


def minutes_label(seconds: float) -> str:
    """Суммарная длительность речи: '0 мин', '3 мин'."""
    minutes = max(0, int(math.ceil(float(seconds or 0.0) / 60.0)))
    return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"


# --------------------------------------------------------------------------- #
# Модели/движки
# --------------------------------------------------------------------------- #
def engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(engine or "", engine or "Не выбран")


def model_title(cfg) -> str:
    """Подпись активной модели: 'GigaAM-v3 · e2e_rnnt' / 'faster-whisper · large-v3-turbo'."""
    engine = getattr(cfg.asr, "engine", "gigaam")
    if engine == "gigaam":
        return f"GigaAM-v3 · {getattr(cfg.asr, 'gigaam_variant', 'e2e_rnnt')}"
    if engine == "whisper":
        return f"faster-whisper · {getattr(cfg.asr, 'whisper_model', 'large-v3-turbo')}"
    if engine == "cloud":
        return f"Облако · {getattr(cfg.asr, 'cloud_model', 'gpt-4o-transcribe')}"
    return engine_label(engine)


def engine_status_text(status: dict) -> str:
    """Текст статуса модели из controller.engine_status()."""
    status = status or {}
    engine = engine_label(str(status.get("engine", "")))
    if status.get("loading"):
        return status.get("message") or f"{engine}: загрузка модели…"
    if status.get("loaded"):
        return status.get("message") or f"{engine}: модель загружена"
    return status.get("message") or f"{engine}: модель не загружена"


def provider_label(provider: str) -> str:
    return {
        "deepseek": "DeepSeek",
        "openai": "OpenAI",
        "custom": "Другой OpenAI-совместимый сервис",
    }.get(provider or "", provider or "—")


def env_status_text(env_name: str, is_set: bool) -> str:
    if not env_name:
        return "Имя переменной с ключом не задано."
    if is_set:
        return f"Ключ найден в переменной окружения {env_name}."
    return f"Переменная окружения {env_name} не задана."
