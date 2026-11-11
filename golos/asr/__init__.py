"""Движки распознавания речи (GigaAM-v3, faster-whisper, облачный API).

Импорт пакета лёгкий: тяжёлые зависимости (torch, transformers, faster-whisper)
подтягиваются только внутри ``load()`` соответствующего движка. Поэтому UI может
импортировать ``golos.asr`` на старте, не тратя секунды на torch.
"""

from __future__ import annotations

from .base import ASREngine, ProgressFn, Transcript
from .registry import (
    DEFAULT_ENGINE,
    ENGINES,
    ENGINE_TITLES,
    create_engine,
    download_model,
    engine_info,
    engine_is_installed,
    gigaam_model_dir,
    gigaam_model_installed,
    model_dirname,
)

__all__ = [
    "ASREngine",
    "ProgressFn",
    "Transcript",
    "DEFAULT_ENGINE",
    "ENGINES",
    "ENGINE_TITLES",
    "create_engine",
    "download_model",
    "engine_info",
    "engine_is_installed",
    "gigaam_model_dir",
    "gigaam_model_installed",
    "model_dirname",
]
