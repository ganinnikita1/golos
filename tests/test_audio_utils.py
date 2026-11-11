"""Тесты аудио-утилит: моно, ресемплинг, нормализация, VAD, WAV."""

from __future__ import annotations

import numpy as np

from golos.audio import utils


def test_to_mono_from_channels() -> None:
    stereo = np.stack([np.ones(100, np.float32), np.zeros(100, np.float32)], axis=1)
    mono = utils.to_mono(stereo)
    assert mono.shape == (100,)
    assert np.allclose(mono, 0.5)


def test_to_mono_from_flat() -> None:
    flat = np.ones(50, np.float32)
    assert utils.to_mono(flat).shape == (50,)


def test_resample_changes_rate() -> None:
    sr_in, sr_out = 48000, 16000
    t = np.arange(sr_in, dtype=np.float32) / sr_in
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    resampled = utils.resample_linear(audio, sr_in, sr_out)
    assert abs(resampled.shape[0] - sr_out) <= 1
    assert np.max(np.abs(resampled)) < 1.01


def test_resample_same_rate_is_noop() -> None:
    audio = np.zeros(100, np.float32)
    assert utils.resample_linear(audio, 16000, 16000).shape == (100,)


def test_rms_and_peak() -> None:
    assert utils.rms(np.zeros(10, np.float32)) == 0.0
    assert utils.rms(np.ones(10, np.float32)) == 1.0
    assert utils.peak(np.array([0.2, -0.5], np.float32)) == 0.5


def test_normalize_peak_raises_quiet_signal() -> None:
    quiet = np.full(1000, 0.05, np.float32)
    loud = utils.normalize(quiet, "peak")
    assert utils.peak(loud) > 0.9
    assert np.all(np.abs(loud) <= 1.0)


def test_normalize_does_not_clip_loud_signal() -> None:
    loud = np.array([0.2, -0.4, 0.9], np.float32)
    out = utils.normalize(loud, "peak")
    assert np.all(np.abs(out) <= 1.0)
    assert np.max(np.abs(out)) <= 1.0
    # форма сигнала сохраняется (пропорции между отсчётами)
    assert np.allclose(out / np.max(np.abs(out)), loud / np.max(np.abs(loud)), atol=1e-4)


def test_normalize_off_keeps_signal() -> None:
    audio = np.full(100, 0.1, np.float32)
    assert np.array_equal(utils.normalize(audio, "off"), audio)


def test_normalize_silence_does_not_explode() -> None:
    silence = np.zeros(100, np.float32)
    assert np.all(utils.normalize(silence, "peak") == 0)


def test_db_level_range() -> None:
    assert utils.db_level(np.zeros(100, np.float32)) == 0.0
    loud = utils.db_level(np.ones(100, np.float32) * 0.5)
    assert 0.0 < loud <= 1.0


def test_trim_silence_removes_padding() -> None:
    sr = 16000
    silence = np.zeros(sr, np.float32)
    tone = (0.3 * np.sin(2 * np.pi * 300 * np.arange(sr, dtype=np.float32) / sr)).astype(np.float32)
    audio = np.concatenate([silence, tone, silence])
    trimmed = utils.trim_silence(audio, sr, margin_ms=50)
    assert 0 < trimmed.shape[0] < audio.shape[0]
    assert trimmed.shape[0] >= sr * 0.9  # тон не обрезан


def test_trim_silence_all_silence_returns_empty() -> None:
    trimmed = utils.trim_silence(np.zeros(16000, np.float32), 16000)
    assert trimmed.size == 0


def test_wav_roundtrip(tmp_path) -> None:
    sr = 16000
    audio = (0.5 * np.sin(2 * np.pi * 200 * np.arange(sr, dtype=np.float32) / sr)).astype(np.float32)
    path = utils.write_wav(tmp_path / "a.wav", audio, sr)
    loaded, rate = utils.read_wav(path)
    assert rate == sr
    assert loaded.shape[0] == audio.shape[0]
    assert abs(utils.rms(loaded) - utils.rms(audio)) < 0.01


def test_rms_frames() -> None:
    audio = np.ones(1000, np.float32)
    frames = utils.rms_frames(audio, 100)
    assert frames.shape == (10,)
    assert np.allclose(frames, 1.0)
