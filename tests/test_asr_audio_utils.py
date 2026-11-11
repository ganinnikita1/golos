"""Юнит-тесты аудио-утилит ASR: ресемплинг, нарезка, склейка, WAV."""

from __future__ import annotations

import wave
import io

import numpy as np

from golos.asr.audio_utils import (
    DEFAULT_SR,
    chunk_audio,
    encode_wav,
    merge_texts,
    resample,
    to_mono_float32,
)


def test_to_mono_float32_empty_and_none():
    assert to_mono_float32(None).size == 0
    assert to_mono_float32([]).size == 0
    assert to_mono_float32(np.zeros(0, dtype=np.float32)).size == 0


def test_to_mono_float32_int16_scale():
    data = np.array([0, 32767, -32768], dtype=np.int16)
    out = to_mono_float32(data)
    assert out.dtype == np.float32
    assert out.shape == (3,)
    assert abs(out[1] - 1.0) < 1e-3
    assert out[2] == -1.0 or abs(out[2] + 1.0) < 1e-3


def test_to_mono_float32_stereo_average():
    left = np.ones(100, dtype=np.float32)
    right = -np.ones(100, dtype=np.float32)
    stereo = np.stack([left, right], axis=1)  # (100, 2)
    out = to_mono_float32(stereo)
    assert out.shape == (100,)
    assert np.allclose(out, 0.0, atol=1e-6)


def test_resample_same_rate_is_identity():
    audio = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    out = resample(audio, 16000, 16000)
    assert out.shape == audio.shape
    assert np.allclose(out, audio)


def test_resample_8k_to_16k_length_and_signal():
    sr = 8000
    t = np.arange(sr, dtype=np.float32) / sr
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = resample(audio, 8000, DEFAULT_SR)
    assert abs(len(out) - 16000) <= 2
    assert out.dtype == np.float32
    # Сигнал должен остаться синусоидой той же амплитуды.
    rms_in = float(np.sqrt(np.mean(audio**2)))
    rms_out = float(np.sqrt(np.mean(out**2)))
    assert abs(rms_out - rms_in) < 0.1 * max(rms_in, 1e-6)


def test_resample_empty():
    out = resample(np.zeros(0, dtype=np.float32), 8000, 16000)
    assert out.size == 0


def test_chunk_audio_short_is_single_chunk():
    audio = np.zeros(16000 * 3, dtype=np.float32)
    chunks = chunk_audio(audio, 16000, max_chunk_s=20.0, overlap_s=0.5)
    assert len(chunks) == 1
    assert chunks[0].size == audio.size


def test_chunk_audio_empty():
    assert chunk_audio(np.zeros(0, dtype=np.float32), 16000, 20.0) == []


def test_chunk_audio_long_covers_everything_with_overlap():
    n = 16000 * 50
    audio = np.arange(n, dtype=np.float32)  # уникальные значения = индексы
    chunks = chunk_audio(audio, 16000, max_chunk_s=20.0, overlap_s=0.5)
    assert len(chunks) >= 3
    assert float(chunks[0][0]) == 0.0
    assert float(chunks[-1][-1]) == n - 1
    max_len = 16000 * 20
    for chunk in chunks:
        # допускаем небольшое превышение из-за приклеенного хвоста
        assert chunk.size <= max_len + 16000
    # стыки перекрываются: последний сэмпл чанка >= первого сэмпла следующего
    for previous, following in zip(chunks, chunks[1:]):
        assert float(previous[-1]) >= float(following[0])


def test_chunk_audio_merges_tiny_tail():
    # 20.3 c при max=20 c и overlap=0.5 c даёт хвостовой чанк 0.8 c (< 1 c) —
    # он должен приклеиться к предыдущему, а не распознаваться отдельно.
    n = 16000 * 20 + 16000 * 3 // 10
    audio = np.zeros(n, dtype=np.float32)
    chunks = chunk_audio(audio, 16000, max_chunk_s=20.0, overlap_s=0.5)
    assert len(chunks) == 1


def test_merge_texts_deduplicates_overlap():
    merged = merge_texts(["привет как дела", "как дела сегодня", "сегодня хорошая погода"])
    assert merged == "привет как дела сегодня хорошая погода"


def test_merge_texts_handles_case_and_punctuation():
    merged = merge_texts(["Мы обсудили бюджет.", "Бюджет на следующий квартал утверждён."])
    assert merged == "Мы обсудили бюджет. На следующий квартал утверждён." or (
        merged.lower().count("бюджет") == 1
    )


def test_merge_texts_no_duplicates_when_same_text():
    merged = merge_texts(["Привет, мир.", "Привет, мир."])
    assert merged.lower().count("привет") == 1


def test_merge_texts_empty_parts():
    assert merge_texts([]) == ""
    assert merge_texts(["", "  "]) == ""
    assert merge_texts(["", "привет"]) == "привет"
    assert merge_texts(["привет", ""]) == "привет"


def test_merge_texts_punctuation_join():
    merged = merge_texts(["первая часть", ", вторая часть"])
    assert merged == "первая часть, вторая часть"


def test_encode_wav_header_and_frames():
    audio = np.zeros(8000, dtype=np.float32)
    wav_bytes = encode_wav(audio, 16000)
    assert wav_bytes[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 8000


def test_encode_wav_clips_without_overflow():
    audio = np.array([5.0, -5.0], dtype=np.float32)
    data = encode_wav(audio, 16000)
    with wave.open(io.BytesIO(data), "rb") as wav:
        frames = np.frombuffer(wav.readframes(2), dtype=np.int16)
    assert frames[0] == 32767
    assert frames[1] == -32767
