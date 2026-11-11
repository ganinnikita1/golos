"""Короткие звуковые сигналы начала/конца записи (без внешних зависимостей)."""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np

from .. import paths
from ..logging_setup import get_logger

logger = get_logger(__name__)

_SOUNDS: dict[str, Path] = {}
_LOCK = threading.Lock()


def _synth(freq: float, ms: int, sample_rate: int = 44100, volume: float = 0.18) -> np.ndarray:
    """Синус с мягкой огибающей (без щелчков)."""
    total = int(sample_rate * ms / 1000)
    t = np.arange(total, dtype=np.float64) / sample_rate
    wave = np.sin(2.0 * math.pi * freq * t)
    fade = max(1, int(total * 0.12))
    envelope = np.ones(total)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * envelope * volume).astype(np.float32)


def _ensure(name: str, tones: list[tuple[float, int]]) -> Path | None:
    with _LOCK:
        cached = _SOUNDS.get(name)
        if cached and cached.exists():
            return cached
        try:
            from ..audio.utils import wav_bytes  # локальный импорт: избегаем цикла

            samples = [np.zeros(int(44100 * 0.02), dtype=np.float32)]
            for freq, ms in tones:
                samples.append(_synth(freq, ms))
                samples.append(np.zeros(int(44100 * 0.03), dtype=np.float32))
            audio = np.concatenate(samples)
            target = paths.cache_dir() / "sounds" / f"{name}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(wav_bytes(audio, 44100))
            _SOUNDS[name] = target
            return target
        except Exception:
            logger.exception("Не удалось подготовить звук %s", name)
            return None


def _play(path: Path | None) -> None:
    if path is None:
        return
    try:
        import winsound  # noqa: PLC0415

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def play_start() -> None:
    _play(_ensure("start", [(880.0, 55), (1180.0, 45)]))


def play_stop() -> None:
    _play(_ensure("stop", [(1180.0, 45), (880.0, 55)]))


def play_error() -> None:
    _play(_ensure("error", [(420.0, 110), (300.0, 130)]))
