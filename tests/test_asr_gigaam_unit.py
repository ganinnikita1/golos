"""Юнит-тесты GigaAM-движка на фейковой модели (без скачивания весов)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
import torch

from golos.asr.gigaam_engine import GigaAMEngine, model_dirname
from golos.config import default_config


class _FakeDecoding:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def decode(self, head, encoded, lengths):  # noqa: ANN001 - повторяем сигнатуру модели
        self.calls += 1
        assert encoded.shape[0] == 1
        return [self.text]


class _FakeAsr(torch.nn.Module):
    """Минимальная замена GigaAMASR: forward + decoding.decode."""

    def __init__(self, text: str = "Привет, мир.") -> None:
        super().__init__()
        self.head = torch.nn.Identity()
        self.decoding = _FakeDecoding(text)
        self.forward_calls = 0
        self.last_num_samples = 0

    def forward(self, features, lengths):  # noqa: ANN001
        self.forward_calls += 1
        self.last_num_samples = int(features.shape[-1])
        assert features.dtype == torch.float32
        assert features.dim() == 2 and features.shape[0] == 1
        batch = features.shape[0]
        return torch.zeros(batch, 4, 8), torch.full_like(lengths, 2)


def _make_engine(tmp_path, variant: str = "e2e_rnnt", text: str = "Привет, мир."):
    cfg = replace(default_config().asr, gigaam_variant=variant, max_chunk_s=20.0)
    engine = GigaAMEngine(cfg, models_dir=tmp_path)
    fake = _FakeAsr(text)
    engine._torch = torch
    engine._model = fake  # имитируем загруженную модель
    engine._asr = fake
    engine._device = torch.device("cpu")
    engine._dtype = torch.float32
    return engine, fake


def test_attributes_and_model_dir(tmp_path):
    engine, _fake = _make_engine(tmp_path)
    assert engine.name == "gigaam"
    assert engine.is_loaded() is True
    assert engine.model_dir == tmp_path / "GigaAM-v3"
    assert model_dirname(engine.cfg) == "GigaAM-v3"


def test_transcribe_short_audio(tmp_path):
    engine, fake = _make_engine(tmp_path)
    audio = np.zeros(16000, dtype=np.float32)  # 1 секунда 16 кГц
    transcript = engine.transcribe(audio, 16000)
    assert transcript.text == "Привет, мир."
    assert transcript.engine == "gigaam"
    assert transcript.language == "ru"
    assert abs(transcript.duration_s - 1.0) < 1e-3
    assert transcript.elapsed_s >= 0.0
    assert transcript.rtf >= 0.0
    assert fake.forward_calls == 1
    assert fake.decoding.calls == 1
    assert fake.last_num_samples == 16000
    assert transcript.raw == transcript.text


def test_transcribe_resamples_from_other_rate(tmp_path):
    engine, fake = _make_engine(tmp_path)
    audio = np.zeros(8000, dtype=np.float32)  # 1 c на 8 кГц
    transcript = engine.transcribe(audio, 8000)
    assert abs(transcript.duration_s - 1.0) < 0.01
    # в модель должно попасть ~16000 сэмплов (16 кГц)
    assert abs(fake.last_num_samples - 16000) <= 32


def test_transcribe_stereo_is_mixed_to_mono(tmp_path):
    engine, fake = _make_engine(tmp_path)
    left = np.zeros(16000, dtype=np.float32)
    right = np.zeros(16000, dtype=np.float32)
    stereo = np.stack([left, right], axis=1)
    engine.transcribe(stereo, 16000)
    assert fake.forward_calls == 1
    assert fake.last_num_samples == 16000


def test_long_audio_is_chunked_and_merged(tmp_path):
    engine, fake = _make_engine(tmp_path, text="Привет, мир.")
    audio = np.zeros(16000 * 45, dtype=np.float32)  # 45 c > max_chunk_s=20
    transcript = engine.transcribe(audio, 16000)
    # 45 c режется на 3 чанка: 20 + 20 + 6 c
    assert fake.forward_calls == 3
    assert fake.decoding.calls == 3
    # одинаковые куски текста не должны дублироваться
    assert transcript.text.lower().count("привет") == 1
    assert abs(transcript.duration_s - 45.0) < 1e-3


def test_empty_audio_returns_empty_transcript_without_calls(tmp_path):
    engine, fake = _make_engine(tmp_path)
    transcript = engine.transcribe(np.zeros(0, dtype=np.float32), 16000)
    assert transcript.text == ""
    assert transcript.duration_s == 0.0
    assert fake.forward_calls == 0


def test_transcribe_without_load_raises(tmp_path):
    cfg = default_config().asr
    engine = GigaAMEngine(cfg, models_dir=tmp_path)
    assert engine.is_loaded() is False
    with pytest.raises(RuntimeError, match="не загружена"):
        engine.transcribe(np.zeros(1600, dtype=np.float32), 16000)


def test_unload_clears_state(tmp_path):
    engine, _fake = _make_engine(tmp_path)
    engine.unload()
    assert engine.is_loaded() is False
    with pytest.raises(RuntimeError):
        engine.transcribe(np.zeros(1600, dtype=np.float32), 16000)


def test_hf_cache_redirected_into_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLOS_LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("GOLOS_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("HF_HOME", raising=False)
    engine, _fake = _make_engine(tmp_path)
    engine._configure_hf_cache()
    from golos import paths

    import os

    assert os.environ["HF_HOME"] == str(paths.cache_dir() / "huggingface")
    monkeypatch.delenv("HF_HOME", raising=False)


def test_hf_cache_respects_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLOS_LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HF_HOME", "D:/custom-hf")
    engine, _fake = _make_engine(tmp_path)
    engine._configure_hf_cache()
    import os

    assert os.environ["HF_HOME"] == "D:/custom-hf"
