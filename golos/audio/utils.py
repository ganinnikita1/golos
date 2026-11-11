"""Утилиты для аудио: ресемплинг, нормализация, отсечение тишины, WAV."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

#: Целевая частота для ASR (GigaAM/Whisper обучены на 16 кГц).
TARGET_RATE = 16000


def to_mono(audio: np.ndarray) -> np.ndarray:
    """(N, C) -> (N,)."""
    if audio.ndim == 2:
        if audio.shape[0] < audio.shape[1]:  # (C, N) от sounddevice
            audio = audio.T
        return audio.mean(axis=1)
    return audio.ravel()


def resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    """Линейный ресемплинг — достаточно для речи, быстро и без зависимостей."""
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    duration = audio.shape[0] / float(src_rate)
    dst_len = max(1, int(round(duration * dst_rate)))
    src_idx = np.linspace(0.0, audio.shape[0] - 1, dst_len, dtype=np.float64)
    out = np.interp(src_idx, np.arange(audio.shape[0], dtype=np.float64), audio)
    return out.astype(np.float32, copy=False)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def peak(audio: np.ndarray) -> float:
    return float(np.max(np.abs(audio))) if audio.size else 0.0


def normalize(audio: np.ndarray, mode: str = "peak", target: float = 0.95) -> np.ndarray:
    """Приводит громкость к удобному для модели уровню. Без клиппинга."""
    audio = audio.astype(np.float32, copy=False)
    if audio.size == 0 or mode == "off":
        return audio
    if mode == "peak":
        level = peak(audio)
        if level > 1e-4:
            gain = min(32.0, target / level)
            return np.clip(audio * gain, -1.0, 1.0).astype(np.float32, copy=False)
        return audio
    if mode == "rms":
        level = rms(audio)
        if level > 1e-4:
            gain = min(16.0, 0.08 / level)
            return np.clip(audio * gain, -1.0, 1.0).astype(np.float32, copy=False)
        return audio
    return audio


def db_level(audio: np.ndarray, floor_db: float = -60.0, ceil_db: float = -6.0) -> float:
    """Уровень сигнала для индикатора: 0..1 по логарифмической шкале."""
    level = rms(audio)
    if level <= 0:
        return 0.0
    db = 20.0 * np.log10(level + 1e-12)
    scaled = (db - floor_db) / (ceil_db - floor_db)
    return float(np.clip(scaled, 0.0, 1.0))


def wav_bytes(audio: np.ndarray, sample_rate: int = TARGET_RATE, width: int = 2) -> bytes:
    """float32 mono -> WAV (PCM16) в памяти."""
    audio = np.asarray(audio, dtype=np.float32).ravel()
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * (2 ** (8 * width - 1) - 1)).astype("<i2" if width == 2 else "<i4")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(width)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
    return buf.getvalue()


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int = TARGET_RATE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(audio, sample_rate))
    return path


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Читает WAV в float32 mono. Через soundfile (если есть), иначе wave."""
    try:
        import soundfile as sf  # noqa: PLC0415

        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        return to_mono(data), int(rate)
    except Exception:
        with wave.open(str(path), "rb") as fh:
            frames = fh.readframes(fh.getnframes())
            rate = fh.getframerate()
            width = fh.getsampwidth()
            channels = fh.getnchannels()
        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
        data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
        if width == 1:
            data = (data - 128.0) / 128.0
        elif width == 2:
            data = data / 32768.0
        else:
            data = data / 2147483648.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data.astype(np.float32), int(rate)


def trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    *,
    margin_ms: int = 150,
    frame_ms: int = 20,
    threshold_factor: float = 3.0,
    min_abs_threshold: float = 0.004,
) -> np.ndarray:
    """Обрезает тишину по краям с адаптивным порогом от шумового пола."""
    if audio.size == 0:
        return audio
    frame = max(1, int(sample_rate * frame_ms / 1000))
    frame_rms = rms_frames(audio, frame)
    if frame_rms.size == 0:
        return audio
    noise_floor = float(np.percentile(frame_rms, 15))
    threshold = max(min_abs_threshold, noise_floor * threshold_factor)
    voiced = np.flatnonzero(frame_rms >= threshold)
    if voiced.size == 0:
        # Полная тишина: вернуть пустой массив, чтобы ASR не гадал.
        return np.zeros(0, dtype=np.float32)
    start = int(voiced[0] * frame)
    end = int(min(audio.shape[0], (voiced[-1] + 1) * frame))
    margin = int(sample_rate * margin_ms / 1000)
    return audio[max(0, start - margin): min(audio.shape[0], end + margin)]


def rms_frames(audio: np.ndarray, frame: int) -> np.ndarray:
    """RMS по непересекающимся кадрам."""
    usable = audio.shape[0] - (audio.shape[0] % frame)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    shaped = audio[:usable].reshape(-1, frame)
    return np.sqrt(np.mean(np.square(shaped, dtype=np.float64), axis=1)).astype(np.float32)
