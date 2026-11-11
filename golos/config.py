"""Конфигурация приложения: dataclasses + JSON-персистенция.

Правила:
* Любое поле имеет безопасное значение по умолчанию — приложение обязано работать
  на пустой/битой конфигурации.
* Неизвестные ключи в JSON игнорируются, отсутствующие берутся из дефолтов.
* Запись атомарная (tmp + os.replace), чтобы не терять настройки при падении.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_origin as typing_get_origin, get_type_hints

from . import paths

CONFIG_VERSION = 1


# --------------------------------------------------------------------------- #
# Аудио
# --------------------------------------------------------------------------- #
@dataclass
class AudioConfig:
    #: Пустой/None — системный микрофон по умолчанию.
    input_device: str | None = None
    sample_rate: int = 16000
    channels: int = 1
    #: Короче этого запись считается случайным нажатием и не распознаётся.
    min_duration_s: float = 0.35
    #: Максимум записи: дальше автостоп (0 — без ограничения).
    max_duration_s: float = 180.0
    #: Отсечение тишины по краям (мс).
    trim_silence_ms: int = 250
    #: VAD: energy (по умолчанию) | silero | off.
    vad: str = "energy"
    vad_threshold: float = 0.45
    #: Усиление/нормализация: off | peak | rms.
    normalize: str = "peak"


# --------------------------------------------------------------------------- #
# Распознавание
# --------------------------------------------------------------------------- #
@dataclass
class ASRConfig:
    #: gigaam | whisper | cloud
    engine: str = "gigaam"
    #: Язык распознавания. Для GigaAM не используется (модель русская),
    #: для whisper/cloud передаётся как подсказка.
    language: str = "ru"

    # --- GigaAM-v3 (ai-sage/GigaAM-v3, MIT) ---
    gigaam_repo: str = "ai-sage/GigaAM-v3"
    #: ssl | ctc | rnnt | e2e_ctc | e2e_rnnt. e2e_* отдают текст с пунктуацией.
    gigaam_variant: str = "e2e_rnnt"
    gigaam_dirname: str = "GigaAM-v3"
    #: Треды torch intra-op (0 = авто, физические ядра).
    torch_threads: int = 0
    #: Длинное аудио режем на куски не длиннее этого (сек).
    max_chunk_s: float = 20.0
    #: Сверять SHA256 файлов модели с проверенным манифестом перед загрузкой.
    #: При несовпадении модель не загружается (remote-код не исполняется).
    verify_model_hashes: bool = True
    #: Скачивать модель с неизменяемого commit SHA из манифеста, а не с ветки-ревизии.
    pin_model_revision: bool = True
    #: Разрешить загрузку модели из пользовательского репозитория без проверенных
    #: хешей (по умолчанию запрещено: чужой remote-код не должен исполняться).
    allow_unverified_model: bool = False

    # --- faster-whisper ---
    whisper_model: str = "large-v3-turbo"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 1

    # --- облачный ASR (OpenAI-совместимый /audio/transcriptions) ---
    cloud_provider: str = "openai"  # openai | groq | custom
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_model: str = "gpt-4o-transcribe"
    cloud_api_key_env: str = "OPENAI_API_KEY"
    cloud_timeout_s: float = 30.0


# --------------------------------------------------------------------------- #
# Текст
# --------------------------------------------------------------------------- #
@dataclass
class DictionaryEntry:
    """Замена, которую пользователь задаёт вручную (регистронезависимо)."""

    source: str
    target: str
    #: Совпадение по целому слову, а не подстроке.
    whole_word: bool = True


def _default_filler_words() -> list[str]:
    return ["э", "ээ", "эээ", "эм", "мм", "м", "ну", "как бы", "типа", "в общем-то"]


@dataclass
class TextConfig:
    #: Пунктуация: e2e-модель уже ставит знаки; выключатель для сырых моделей.
    punctuation: bool = True
    #: Всегда начинать предложение с заглавной.
    capitalize: bool = True
    #: Убрать слова-паразиты («ну», «э-э», «как бы»).
    remove_fillers: bool = False
    filler_words: list[str] = field(default_factory=_default_filler_words)
    #: Произнесённые знаки: «запятая» → ",".
    spoken_punctuation: bool = False
    #: Добавить пробел после готового текста.
    trailing_space: bool = True
    #: Убрать пробелы перед знаками и лишние пробелы.
    tidy_spaces: bool = True
    dictionary: list[DictionaryEntry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# LLM-полировка (опционально; по умолчанию выключена)
# --------------------------------------------------------------------------- #
@dataclass
class PolishConfig:
    enabled: bool = False
    #: Любой OpenAI-совместимый chat/completions endpoint.
    provider: str = "deepseek"  # deepseek | openai | custom
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    #: dictation — исправить пунктуацию/регистр/опечатки, сохранив смысл;
    #: literal — почти ничего не менять, только знаки.
    style: str = "dictation"
    timeout_s: float = 8.0
    #: Если LLM не ответила за timeout — используем необработанный текст.
    fallback_to_raw: bool = True


# --------------------------------------------------------------------------- #
# Вставка текста
# --------------------------------------------------------------------------- #
@dataclass
class InsertConfig:
    #: type — прямой Unicode-ввод через SendInput: буфер обмена вообще не трогается
    #: (медленнее, зато диктовка никогда не попадает в буфер и журнал Win+V);
    #: clipboard — через буфер: быстро и совместимо, содержимое буфера возвращается,
    #: а сам текст помечается приватным и не сохраняется в журнале Windows.
    method: str = "type"
    #: Возвращать прежнее содержимое буфера обмена после вставки (только clipboard).
    restore_clipboard: bool = True
    #: Не оставлять распознанный текст в журнале буфера обмена Windows (Win+V)
    #: и не синхронизировать его в облако (только clipboard).
    exclude_from_history: bool = True
    #: Задержка после копирования в буфер перед Ctrl+V (мс).
    paste_delay_ms: int = 60
    #: Нажать Enter после вставки (для чатов).
    press_enter_after: bool = False


# --------------------------------------------------------------------------- #
# Горячая клавиша
# --------------------------------------------------------------------------- #
@dataclass
class HotkeyConfig:
    #: Спецификация вида "ctrl+alt+space", "f9", "right alt".
    hotkey: str = "ctrl+alt+space"
    #: toggle — нажал/нажал; push_to_talk — держать и отпустить.
    mode: str = "toggle"  # toggle | push_to_talk
    #: Вторая клавиша для отмены текущей записи-распознавания.
    cancel_key: str = "esc"


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
@dataclass
class UIConfig:
    language: str = "ru"
    theme: str = "dark"  # dark | light | system
    overlay_enabled: bool = True
    #: bottom_center | top_center | bottom_right | near_cursor
    overlay_position: str = "bottom_center"
    show_history_after_insert: bool = False
    start_minimized: bool = True
    #: Автозапуск при входе в Windows.
    autostart: bool = False
    #: Звуковой сигнал начала/конца записи.
    sounds: bool = True


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    text: TextConfig = field(default_factory=TextConfig)
    polish: PolishConfig = field(default_factory=PolishConfig)
    insert: InsertConfig = field(default_factory=InsertConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    history_limit: int = 200
    #: Сохранять историю распознаваний на диск (можно отключить для приватности).
    history_enabled: bool = True
    log_level: str = "INFO"
    #: Первый запуск / мастер настройки пройден.
    onboarding_done: bool = False


# --------------------------------------------------------------------------- #
# (de)serialization
# --------------------------------------------------------------------------- #
def _from_dict(cls: type, data: Any) -> Any:
    """Заполняет dataclass из dict, игнорируя неизвестные и битые поля."""
    if not dataclasses.is_dataclass(cls) or not isinstance(data, dict):
        return data
    # from __future__ import annotations превращает типы в строки — резолвим их.
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {}
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        declared = hints.get(f.name, f.type)
        origin = typing_get_origin(declared)
        try:
            if dataclasses.is_dataclass(declared):
                kwargs[f.name] = _from_dict(declared, value)
            elif f.name == "dictionary":
                kwargs[f.name] = [
                    DictionaryEntry(source=item.get("source", ""), target=item.get("target", ""),
                                    whole_word=bool(item.get("whole_word", True)))
                    for item in value
                    if isinstance(item, dict) and item.get("source")
                ]
            elif origin is list or declared is list:
                kwargs[f.name] = list(value) if isinstance(value, list) else list(f.default)
            elif declared is bool:
                kwargs[f.name] = bool(value)
            elif declared is int:
                kwargs[f.name] = int(value)
            elif declared is float:
                kwargs[f.name] = float(value)
            else:
                kwargs[f.name] = value
        except (TypeError, ValueError):
            continue
    return cls(**kwargs)


def default_config() -> AppConfig:
    return AppConfig()


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    return dataclasses.asdict(cfg)


def load_config(path: Path | None = None) -> AppConfig:
    """Читает конфиг; при любой ошибке возвращает дефолтный.

    utf-8-sig: пользователи и внешние редакторы (в т.ч. PowerShell) часто
    сохраняют JSON с BOM — это не должно ломать настройки.
    """
    path = path or paths.config_path()
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except FileNotFoundError:
        return default_config()
    except (OSError, ValueError):
        return default_config()
    if not isinstance(data, dict):
        return default_config()
    cfg = _from_dict(AppConfig, data)
    return _validate(cfg)


def _validate(cfg: AppConfig) -> AppConfig:
    """Мягкая нормализация: чиним явно некорректные значения."""
    if cfg.asr.engine not in {"gigaam", "whisper", "cloud"}:
        cfg.asr.engine = "gigaam"
    if cfg.asr.gigaam_variant not in {"ssl", "ctc", "rnnt", "e2e_ctc", "e2e_rnnt"}:
        cfg.asr.gigaam_variant = "e2e_rnnt"
    # Репозиторий и имя каталога модели приходят из конфига — не даём увести
    # загрузку за пределы models_dir (path traversal) и подменить источник.
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}", cfg.asr.gigaam_repo or ""):
        cfg.asr.gigaam_repo = "ai-sage/GigaAM-v3"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cfg.asr.gigaam_dirname or ""):
        cfg.asr.gigaam_dirname = "GigaAM-v3"
    cfg.asr.whisper_model = str(cfg.asr.whisper_model or "large-v3-turbo")[:200]
    if cfg.audio.vad not in {"silero", "energy", "off"}:
        cfg.audio.vad = "energy"
    if cfg.audio.normalize not in {"off", "peak", "rms"}:
        cfg.audio.normalize = "peak"
    if cfg.insert.method not in {"clipboard", "type"}:
        cfg.insert.method = "type"
    if cfg.hotkey.mode not in {"toggle", "push_to_talk"}:
        cfg.hotkey.mode = "toggle"
    if cfg.audio.sample_rate not in {8000, 16000, 22050, 44100, 48000}:
        cfg.audio.sample_rate = 16000
    cfg.audio.min_duration_s = max(0.1, min(5.0, cfg.audio.min_duration_s))
    cfg.audio.max_duration_s = max(5.0, cfg.audio.max_duration_s)
    cfg.audio.trim_silence_ms = max(0, min(2000, cfg.audio.trim_silence_ms))
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> Path:
    path = path or paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config_to_dict(cfg), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path


def update_config(cfg: AppConfig, **patches: Any) -> AppConfig:
    """Неглубокая правка секций: update_config(cfg, audio={"vad": "energy"})."""
    new = copy.deepcopy(cfg)
    for section, values in patches.items():
        target = getattr(new, section)
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)
    return _validate(new)
