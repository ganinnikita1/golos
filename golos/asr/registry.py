"""Фабрика ASR-движков + проверка/скачивание моделей для UI и мастера запуска.

``create_engine()`` — единственная точка выбора движка для пайплайна.
``engine_is_installed()`` / ``download_model()`` нужны интерфейсу, чтобы
показать статус модели и кнопку «Скачать» ещё до первой диктовки.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import ASRConfig
from .base import ASREngine, ProgressFn
from .cloud_engine import CloudEngine
from .gigaam_engine import (
    GigaAMEngine,
    gigaam_model_dir,
    gigaam_model_installed,
    model_dirname,
)
from .whisper_engine import WhisperEngine

logger = logging.getLogger(__name__)

ENGINES = ("gigaam", "whisper", "cloud")

#: Движок по умолчанию, если в конфиге что-то неизвестное.
DEFAULT_ENGINE = "gigaam"

#: Что показывать пользователю в UI.
ENGINE_TITLES = {
    "gigaam": "GigaAM-v3 (офлайн, Россия)",
    "whisper": "faster-whisper (офлайн, CTranslate2)",
    "cloud": "Облачное распознавание (OpenAI-совместимое)",
}


def create_engine(cfg: ASRConfig, models_dir: Path | str | None = None) -> ASREngine:
    """Создаёт движок по ``cfg.engine``. Не загружает модель."""
    name = (cfg.engine or DEFAULT_ENGINE).strip().lower()
    if name == "gigaam":
        return GigaAMEngine(cfg, models_dir=models_dir)
    if name == "whisper":
        return WhisperEngine(cfg, models_dir=models_dir)
    if name == "cloud":
        return CloudEngine(cfg, models_dir=models_dir)
    raise RuntimeError(
        f"Неизвестный движок распознавания «{name}». "
        f"Допустимые: {', '.join(ENGINES)}."
    )


def engine_is_installed(cfg: ASRConfig, models_dir: Path | str | None = None) -> bool:
    """Готов ли движок к работе без дополнительного скачивания/настройки."""
    name = (cfg.engine or DEFAULT_ENGINE).strip().lower()
    if name == "gigaam":
        return gigaam_model_installed(cfg, models_dir)
    if name == "whisper":
        return WhisperEngine(cfg, models_dir).installed()
    if name == "cloud":
        return CloudEngine(cfg).api_key() is not None
    return False


def download_model(
    cfg: ASRConfig,
    progress: ProgressFn | None = None,
    models_dir: Path | str | None = None,
) -> None:
    """Скачивает модель текущего движка (для GigaAM — с прогрессом)."""
    engine = create_engine(cfg, models_dir=models_dir)
    if engine_is_installed(cfg, models_dir):
        if progress is not None:
            progress("Модель уже скачана", 1.0)
        return
    engine.load(progress)


def engine_info(
    cfg: ASRConfig, models_dir: Path | str | None = None
) -> dict[str, Any]:
    """Краткий статус для UI: что выбрано, скачано ли, где лежит."""
    name = (cfg.engine or DEFAULT_ENGINE).strip().lower()
    info: dict[str, Any] = {
        "engine": name,
        "title": ENGINE_TITLES.get(name, name),
        "installed": engine_is_installed(cfg, models_dir),
        "variant": cfg.gigaam_variant if name == "gigaam" else None,
    }
    if name == "gigaam":
        info["model_dir"] = str(gigaam_model_dir(cfg, models_dir))
    elif name == "whisper":
        info["model_dir"] = str(WhisperEngine(cfg, models_dir).model_dir)
        info["variant"] = cfg.whisper_model
    elif name == "cloud":
        info["model_dir"] = None
        info["variant"] = cfg.cloud_model
        info["api_key_env"] = cfg.cloud_api_key_env
        info["api_key_set"] = CloudEngine(cfg).api_key() is not None
    return info


__all__ = [
    "ENGINES",
    "DEFAULT_ENGINE",
    "ENGINE_TITLES",
    "create_engine",
    "engine_is_installed",
    "download_model",
    "engine_info",
    "gigaam_model_dir",
    "gigaam_model_installed",
    "model_dirname",
]
