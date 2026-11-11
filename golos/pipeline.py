"""Оркестрация диктовки: аудио → распознавание → текст → полировка → вставка.

Пайплайн вызывается из одного рабочего потока (см. golos.app), поэтому внутри
не нужны блокировки на распознавание: одновременно обрабатывается одна запись.
Все состояния и результаты уходят в EventBus; UI читает их оттуда.
"""

from __future__ import annotations

import dataclasses
import importlib
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .asr.base import ASREngine, Transcript
from .audio import utils as audio_utils
from .config import AppConfig
from .events import EventBus
from .history import HistoryStore
from .logging_setup import get_logger

logger = get_logger(__name__)


class SessionState:
    IDLE = "idle"
    LOADING = "loading"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    POLISHING = "polishing"
    INSERTING = "inserting"
    ERROR = "error"


@dataclass(frozen=True)
class DictationResult:
    text: str
    raw_text: str = ""
    engine: str = ""
    duration_s: float = 0.0
    asr_elapsed_s: float = 0.0
    total_elapsed_s: float = 0.0
    inserted: bool = False
    polished: bool = False
    rtf: float = 0.0


@dataclass
class PipelineHooks:
    """Точки расширения, которые подставляет приложение."""

    #: Достаёт (лениво загруженный) движок распознавания.
    engine_provider: Callable[[], ASREngine]
    #: Создаёт инжектор по имени метода (clipboard/type) или None если ещё не готов.
    injector_factory: Callable[[str], object] | None = None
    #: Создаёт LLM-полировщик по конфигу.
    polisher_factory: Callable[[], object] | None = None
    #: Вызывается после успешного распознавания (для звуков/уведомлений).
    on_result: Callable[[DictationResult], None] | None = None
    #: Вставка включена (например, при тестовом прогоне — выключена).
    insert: bool = True


class DictationPipeline:
    def __init__(
        self,
        cfg: AppConfig,
        bus: EventBus,
        history: HistoryStore,
        hooks: PipelineHooks,
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.history = history
        self.hooks = hooks
        self._state = SessionState.IDLE
        self._last_result: DictationResult | None = None

    # ------------------------------------------------------------------ #
    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str, **data) -> None:
        self._state = state
        self.bus.emit("state", state=state, **data)

    def _error(self, message: str, detail: str = "") -> None:
        logger.error("Ошибка диктовки: %s (%s)", message, detail)
        self._set_state(SessionState.ERROR, message=message)
        self.bus.emit("error", message=message, detail=detail)
        self._set_state(SessionState.IDLE)

    # ------------------------------------------------------------------ #
    def run(self, audio: np.ndarray, sample_rate: int) -> DictationResult | None:
        """Полный цикл для готовой записи. Возвращает результат или None."""
        started = time.monotonic()
        if audio is None or audio.size == 0:
            self.bus.emit("toast", message="Ничего не записалось — микрофон молчит?", level="warn")
            return None

        try:
            audio = self._prepare_audio(audio, sample_rate)
        except Exception as exc:  # защита от битых данных
            self._error("Не удалось обработать аудио", str(exc))
            return None

        duration = audio.shape[0] / float(self.cfg.audio.sample_rate)
        if duration < self.cfg.audio.min_duration_s:
            self.bus.emit("toast", message="Слишком коротко — не распознаю", level="info")
            self._set_state(SessionState.IDLE)
            return None

        # --- распознавание ------------------------------------------------
        self._set_state(SessionState.TRANSCRIBING)
        asr_started = time.monotonic()
        engine = self.hooks.engine_provider()
        if engine is None:
            self._error("Движок распознавания не готов", "engine_provider вернул None")
            return None
        try:
            if not engine.is_loaded():
                self._set_state(SessionState.LOADING)
                engine.load(self._progress)
                self._set_state(SessionState.TRANSCRIBING)
            transcript: Transcript = engine.transcribe(audio, self.cfg.audio.sample_rate)
        except Exception as exc:
            logger.exception("ASR упал")
            self._error("Ошибка распознавания речи", str(exc))
            return None
        asr_elapsed = time.monotonic() - asr_started
        raw_text = (transcript.text or "").strip()
        if not raw_text:
            if self.cfg.history_enabled:
                self.history.add_text("", raw_text="", engine=transcript.engine or engine.name,
                                      duration_s=duration, rtf=transcript.rtf, inserted=False)
            self.bus.emit("toast", message="Не расслышал речь", level="warn")
            self._set_state(SessionState.IDLE)
            return None

        # --- текст --------------------------------------------------------
        text = self._postprocess(raw_text)
        polished = False
        if self.cfg.polish.enabled and text:
            text, polished = self._polish(text)

        final_text = text.strip()
        if not final_text:
            self.bus.emit("toast", message="После обработки текст пустой", level="warn")
            self._set_state(SessionState.IDLE)
            return None
        self.bus.emit("partial", text=final_text)

        # --- вставка ------------------------------------------------------
        inserted = False
        if self.hooks.insert and final_text:
            self._set_state(SessionState.INSERTING)
            inserted = self._insert(final_text)
            if not inserted:
                self.bus.emit("toast", message="Текст скопирован в буфер — вставьте вручную",
                              level="warn")

        result = DictationResult(
            text=final_text,
            raw_text=raw_text,
            engine=transcript.engine or engine.name,
            duration_s=duration,
            asr_elapsed_s=asr_elapsed,
            total_elapsed_s=time.monotonic() - started,
            inserted=inserted,
            polished=polished,
            rtf=transcript.rtf,
        )
        self._last_result = result
        if self.cfg.history_enabled:
            try:
                self.history.add_text(
                    final_text,
                    raw_text=raw_text,
                    engine=result.engine,
                    duration_s=duration,
                    rtf=result.rtf,
                    inserted=inserted,
                )
            except Exception:
                logger.exception("Не удалось сохранить историю")
        self.bus.emit(
            "result",
            text=final_text,
            raw_text=raw_text,
            engine=result.engine,
            elapsed_s=result.total_elapsed_s,
            asr_elapsed_s=result.asr_elapsed_s,
            duration_s=duration,
            rtf=result.rtf,
            inserted=inserted,
            polished=polished,
        )
        self.bus.emit("level", value=0.0)
        self._set_state(SessionState.IDLE)
        if self.hooks.on_result is not None:
            try:
                self.hooks.on_result(result)
            except Exception:
                logger.exception("on_result упал")
        return result

    # ------------------------------------------------------------------ #
    def _prepare_audio(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = audio_utils.to_mono(np.asarray(audio, dtype=np.float32))
        if sample_rate != self.cfg.audio.sample_rate:
            audio = audio_utils.resample_linear(audio, sample_rate, self.cfg.audio.sample_rate)
        if self.cfg.audio.vad != "off":
            trimmed = audio_utils.trim_silence(audio, self.cfg.audio.sample_rate,
                                               margin_ms=self.cfg.audio.trim_silence_ms)
            if trimmed.size > 0:
                audio = trimmed
        audio = audio_utils.normalize(audio, self.cfg.audio.normalize)
        return audio

    def _progress(self, message: str, fraction: float | None) -> None:
        self.bus.emit("progress", message=message, fraction=fraction)

    def _postprocess(self, text: str) -> str:
        try:
            module = importlib.import_module("golos.text.postprocess")
            return module.postprocess(text, self.cfg.text)
        except ModuleNotFoundError:
            return self._fallback_postprocess(text)
        except Exception:
            logger.exception("Постобработка текста упала, использую сырой текст")
            return text

    def _fallback_postprocess(self, text: str) -> str:
        """Минимальная чистка, если текстовый модуль недоступен (аварийный режим)."""
        import re

        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"\s+([,.:;!?])", r"\1", cleaned)
        if cleaned and self.cfg.text.capitalize:
            cleaned = cleaned[0].upper() + cleaned[1:]
        if self.cfg.text.trailing_space and cleaned:
            cleaned += " "
        return cleaned

    def _polish(self, text: str) -> tuple[str, bool]:
        factory = self.hooks.polisher_factory
        if factory is None:
            return text, False
        try:
            polisher = factory()
        except Exception:
            logger.exception("Не удалось создать LLM-полировщик")
            return text, False
        if polisher is None or not getattr(polisher, "is_configured", lambda: False)():
            return text, False
        self._set_state(SessionState.POLISHING)
        try:
            polished_text = polisher.polish(text)
        except Exception:
            logger.exception("LLM-полировка упала")
            polished_text = text
        if self.cfg.polish.fallback_to_raw and not polished_text:
            polished_text = text
        return polished_text or text, polished_text != text

    def _insert(self, text: str) -> bool:
        factory = self.hooks.injector_factory
        if factory is None:
            return False
        try:
            injector = factory(self.cfg.insert.method)
        except Exception:
            logger.exception("Не удалось создать инжектор текста")
            return False
        if injector is None:
            return False
        try:
            return bool(injector.inject(text, self.cfg.insert))
        except Exception:
            logger.exception("Вставка текста упала")
            return False
