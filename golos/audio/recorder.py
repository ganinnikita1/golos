"""Запись с микрофона через PortAudio (sounddevice) + уровни сигнала для UI.

Ключевые свойства:
* запись идёт в колбэке PortAudio в фоновом потоке, приложение не блокируется;
* устройство выбирается по имени (устойчиво к переиндексации при смене USB);
* если устройство не поддерживает 16 кГц — пишем на его частоте и ресемплим;
* переполнение буфера не фатально: кадры просто добавляются в список;
* автостоп по max_duration_s через on_auto_stop-колбэк.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from ..config import AudioConfig
from ..events import EventBus
from ..logging_setup import get_logger
from . import utils

logger = get_logger(__name__)


class AudioError(RuntimeError):
    """Понятная пользователю ошибка аудиоустройства."""


def _import_sounddevice():
    try:
        import sounddevice as sd  # noqa: PLC0415

        return sd
    except Exception as exc:  # pragma: no cover - зависит от окружения
        raise AudioError(
            "Не удалось загрузить аудио-библиотеку PortAudio (sounddevice). "
            "Переустановите приложение."
        ) from exc


class Recorder:
    """Одна активная запись за раз; потокобезопасно."""

    def __init__(
        self,
        cfg: AudioConfig,
        bus: EventBus | None = None,
        *,
        on_auto_stop: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.on_auto_stop = on_auto_stop
        self._lock = threading.RLock()
        self._stream = None
        self._frames: list[np.ndarray] = []
        self._device_rate = cfg.sample_rate
        self._recording = False
        self._auto_stopped = False
        self._started_at = 0.0
        self._last_level_emit = 0.0
        self._peak_level = 0.0
        self._frames_written = 0

    # ------------------------------------------------------------------ #
    # Устройства
    # ------------------------------------------------------------------ #
    def devices(self) -> list[dict]:
        """Список входных устройств: [{'index', 'name', 'channels', 'is_default'}]."""
        sd = _import_sounddevice()
        try:
            default_input = None
            try:
                default_input = sd.default.device[0]
            except Exception:
                pass
            result: list[dict] = []
            seen: set[str] = set()
            for index, info in enumerate(sd.query_devices()):
                channels = int(info.get("max_input_channels", 0))
                if channels <= 0:
                    continue
                name = str(info.get("name", f"Устройство {index}")).strip()
                # Одно и то же устройство видно в нескольких host API — берём первое.
                if name in seen:
                    continue
                seen.add(name)
                result.append(
                    {
                        "index": index,
                        "name": name,
                        "channels": channels,
                        "is_default": index == default_input,
                    }
                )
            # Устройство по умолчанию — первым.
            result.sort(key=lambda item: (not item["is_default"], item["name"].lower()))
            return result
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"Не удалось получить список микрофонов: {exc}") from exc

    def resolve_device(self) -> int | None:
        """Имя из конфига -> индекс PortAudio; None = системный по умолчанию."""
        wanted = (self.cfg.input_device or "").strip()
        if not wanted:
            return None
        try:
            for item in self.devices():
                if item["name"] == wanted:
                    return int(item["index"])
        except AudioError:
            return None
        logger.warning("Микрофон %r не найден, использую системный по умолчанию", wanted)
        return None

    # ------------------------------------------------------------------ #
    # Запись
    # ------------------------------------------------------------------ #
    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def auto_stopped(self) -> bool:
        with self._lock:
            return self._auto_stopped

    @property
    def elapsed_s(self) -> float:
        with self._lock:
            if not self._recording:
                return 0.0
            return time.monotonic() - self._started_at

    @property
    def duration_s(self) -> float:
        with self._lock:
            return self._frames_written / float(self._device_rate)

    def start(self) -> None:
        """Открывает поток. Бросает AudioError с человеческим текстом."""
        sd = _import_sounddevice()
        with self._lock:
            if self._recording:
                return
            self._frames = []
            self._frames_written = 0
            self._peak_level = 0.0
            self._auto_stopped = False
            self._started_at = time.monotonic()
            device = self.resolve_device()

            attempts = [(self.cfg.sample_rate, 1), (48000, 1), (44100, 1)]
            last_error: Exception | None = None
            for rate, channels in attempts:
                try:
                    stream = sd.InputStream(
                        samplerate=rate,
                        channels=channels,
                        dtype="float32",
                        device=device,
                        blocksize=0,
                        latency="low",
                        callback=self._callback,
                    )
                    stream.start()
                    self._stream = stream
                    self._device_rate = rate
                    last_error = None
                    break
                except Exception as exc:  # пробуем следующую частоту
                    last_error = exc
                    logger.debug("Не удалось открыть поток %s Гц: %s", rate, exc)
            if self._stream is None:
                raise AudioError(
                    "Не удалось открыть микрофон. Проверьте, что он подключён и не занят "
                    "другим приложением."
                    + (f" ({last_error})" if last_error else "")
                )
            self._recording = True
        logger.info("Запись начата (device=%s, rate=%s)", device, self._device_rate)

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            logger.debug("PortAudio status: %s", status)
        chunk = np.array(indata, dtype=np.float32, copy=True)
        mono = utils.to_mono(chunk)
        with self._lock:
            if not self._recording:
                return
            self._frames.append(mono)
            self._frames_written += mono.shape[0]
            level = utils.db_level(mono)
            self._peak_level = max(self._peak_level, level)
            now = time.monotonic()
            emit_level = (now - self._last_level_emit) >= 0.05
            if emit_level:
                self._last_level_emit = now
            auto_stop = (
                self.cfg.max_duration_s > 0
                and self._frames_written / self._device_rate >= self.cfg.max_duration_s
            )
            if auto_stop:
                self._auto_stopped = True
        if emit_level and self.bus is not None:
            self.bus.emit("level", value=level)
        if auto_stop:
            callback = self.on_auto_stop
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("on_auto_stop упал")

    def stop(self) -> np.ndarray:
        """Останавливает запись и отдаёт float32 mono 16 кГц."""
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            self._recording = False
            stream = self._stream
            self._stream = None
            frames = self._frames
            self._frames = []
            device_rate = self._device_rate
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Ошибка закрытия аудиопотока")
        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames, axis=0)
        if device_rate != self.cfg.sample_rate:
            audio = utils.resample_linear(audio, device_rate, self.cfg.sample_rate)
        logger.info(
            "Запись остановлена: %.2f c, peak level %.2f", audio.shape[0] / self.cfg.sample_rate, self._peak_level
        )
        return audio.astype(np.float32, copy=False)

    def cancel(self) -> None:
        """Прерывает запись, ничего не отдавая."""
        with self._lock:
            self._recording = False
            stream = self._stream
            self._stream = None
            self._frames = []
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Ошибка закрытия аудиопотока при отмене")
        if self.bus is not None:
            self.bus.emit("level", value=0.0)

    def close(self) -> None:
        self.cancel()
