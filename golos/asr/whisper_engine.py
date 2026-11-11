"""Опциональный бэкенд faster-whisper (CTranslate2) для сравнения с GigaAM.

Модель качается самим faster-whisper в ``models_dir/faster-whisper``, поэтому
при отсутствии пакета/модели приложение просто покажет понятную ошибку при
попытке загрузки, а импорт пакета остаётся лёгким.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path

import numpy as np

from .. import paths
from ..config import ASRConfig
from .audio_utils import DEFAULT_SR, resample, to_mono_float32
from .base import ProgressFn, Transcript

logger = logging.getLogger(__name__)


def _whisper_package_available() -> bool:
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


class WhisperEngine:
    """faster-whisper на CPU (int8 по умолчанию) — альтернативный движок."""

    name = "whisper"

    def __init__(self, cfg: ASRConfig, models_dir: Path | str | None = None) -> None:
        self.cfg = cfg
        self._models_dir = Path(models_dir) if models_dir is not None else paths.models_dir()
        self._model = None
        self._lock = threading.RLock()

    @property
    def model_dir(self) -> Path:
        return self._models_dir / "faster-whisper"

    def is_loaded(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------ #
    # установка
    # ------------------------------------------------------------------ #
    def installed(self) -> bool:
        if not _whisper_package_available():
            return False
        root = self.model_dir
        if not root.is_dir():
            return False
        # faster-whisper кладёт модели в подкаталоги вида models--Systran--...
        for child in root.iterdir():
            try:
                if child.is_dir() and any(child.iterdir()):
                    return True
            except OSError:
                continue
        return False

    def load(self, progress: ProgressFn | None = None) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not _whisper_package_available():
                raise RuntimeError(
                    "Не установлен faster-whisper: запустите tools/bootstrap_env.py"
                )
            from faster_whisper import WhisperModel

            self._report(progress, "Загрузка faster-whisper…", 0.1)
            self.model_dir.mkdir(parents=True, exist_ok=True)
            model_name = self.cfg.whisper_model or "large-v3-turbo"
            compute_type = self.cfg.whisper_compute_type or "int8"
            try:
                model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type=compute_type,
                    download_root=str(self.model_dir),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось загрузить faster-whisper '{model_name}' "
                    f"({compute_type}): {exc}"
                ) from exc
            self._model = model
            self._report(progress, "faster-whisper готов", 1.0)
            logger.info("faster-whisper '%s' (%s) загружен", model_name, compute_type)

    # ------------------------------------------------------------------ #
    # распознавание
    # ------------------------------------------------------------------ #
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        if self._model is None:
            raise RuntimeError("faster-whisper не загружен: вызовите load()")
        wav = resample(to_mono_float32(audio), int(sample_rate or DEFAULT_SR), DEFAULT_SR)
        duration = float(wav.size) / DEFAULT_SR
        started = time.perf_counter()
        if wav.size == 0:
            return Transcript(text="", engine=self.name, duration_s=0.0, elapsed_s=0.0, raw="")
        segments, _info = self._model.transcribe(
            wav,
            language=self.cfg.language or "ru",
            beam_size=max(1, int(self.cfg.whisper_beam_size or 1)),
            vad_filter=False,
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        elapsed = time.perf_counter() - started
        text = text.strip()
        return Transcript(
            text=text,
            engine=self.name,
            language=self.cfg.language or "ru",
            duration_s=duration,
            elapsed_s=elapsed,
            raw=text,
        )

    def unload(self) -> None:
        with self._lock:
            self._model = None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _report(progress: ProgressFn | None, message: str, fraction: float | None) -> None:
        if progress is None:
            return
        try:
            progress(message, fraction)
        except Exception:  # pragma: no cover
            logger.debug("progress callback упал", exc_info=True)
