"""Юнит-тесты выбора движков и проверки установки моделей (registry)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from golos.asr.cloud_engine import CloudEngine
from golos.asr.gigaam_engine import GigaAMEngine, gigaam_model_dir, gigaam_model_installed, model_dirname
from golos.asr.registry import (
    create_engine,
    download_model,
    engine_info,
    engine_is_installed,
)
from golos.asr.whisper_engine import WhisperEngine
from golos.config import default_config


@pytest.fixture()
def cfg():
    return default_config().asr


def _write_model_dir(root, variant: str = "e2e_rnnt", marker: bool = True,
                     marker_revision: str | None = None, config_name: str | None = None):
    cfg = replace(default_config().asr, gigaam_variant=variant)
    model_dir = gigaam_model_dir(cfg, root)
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "modeling_gigaam.py").write_text("# remote code\n", encoding="utf-8")
    (model_dir / "tokenizer.model").write_text("tokenizer", encoding="utf-8")
    (model_dir / "pytorch_model.bin").write_bytes(b"weights")
    name = config_name if config_name is not None else f"v3_{variant}"
    config = {"cfg": {"model": {"cfg": {"model_name": name}}}}
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if marker:
        payload = {
            "repo": "ai-sage/GigaAM-v3",
            "revision": marker_revision or variant,
        }
        (model_dir / ".golos_model.json").write_text(json.dumps(payload), encoding="utf-8")
    return model_dir


def test_create_engine_types(cfg):
    assert isinstance(create_engine(cfg), GigaAMEngine)
    assert isinstance(create_engine(replace(cfg, engine="whisper")), WhisperEngine)
    assert isinstance(create_engine(replace(cfg, engine="cloud")), CloudEngine)


def test_create_engine_unknown_raises(cfg):
    with pytest.raises(RuntimeError, match="Неизвестный движок"):
        create_engine(replace(cfg, engine="nonsense"))


def test_engine_names(cfg):
    assert create_engine(cfg).name == "gigaam"
    assert create_engine(replace(cfg, engine="whisper")).name == "whisper"
    assert create_engine(replace(cfg, engine="cloud")).name == "cloud"


def test_model_dirname_default_and_other_variants(cfg):
    assert model_dirname(cfg) == "GigaAM-v3"
    assert model_dirname(replace(cfg, gigaam_variant="ctc")) == "GigaAM-v3-ctc"
    assert model_dirname(replace(cfg, gigaam_variant="e2e_ctc")) == "GigaAM-v3-e2e_ctc"
    assert model_dirname(replace(cfg, gigaam_variant="rnnt")) == "GigaAM-v3-rnnt"


def test_gigaam_model_dir_uses_models_dir(cfg, tmp_path):
    assert gigaam_model_dir(cfg, tmp_path) == tmp_path / "GigaAM-v3"
    assert gigaam_model_dir(replace(cfg, gigaam_variant="ctc"), tmp_path) == tmp_path / "GigaAM-v3-ctc"


def test_gigaam_installed_false_on_empty_dir(cfg, tmp_path):
    assert gigaam_model_installed(cfg, tmp_path) is False
    (tmp_path / "GigaAM-v3").mkdir()
    assert gigaam_model_installed(cfg, tmp_path) is False


def test_gigaam_installed_with_marker(cfg, tmp_path):
    _write_model_dir(tmp_path)
    assert gigaam_model_installed(cfg, tmp_path) is True
    assert engine_is_installed(cfg, tmp_path) is True


def test_gigaam_installed_wrong_revision_marker(cfg, tmp_path):
    _write_model_dir(tmp_path, variant="e2e_rnnt", marker_revision="ctc")
    assert gigaam_model_installed(cfg, tmp_path) is False


def test_gigaam_installed_fallback_config_name(cfg, tmp_path):
    _write_model_dir(tmp_path, variant="e2e_rnnt", marker=False)
    assert gigaam_model_installed(cfg, tmp_path) is True


def test_gigaam_installed_fallback_wrong_config_name(cfg, tmp_path):
    _write_model_dir(tmp_path, variant="e2e_rnnt", marker=False, config_name="v3_ctc")
    assert gigaam_model_installed(cfg, tmp_path) is False


def test_gigaam_installed_missing_weight_file(cfg, tmp_path):
    model_dir = _write_model_dir(tmp_path)
    (model_dir / "pytorch_model.bin").unlink()
    assert gigaam_model_installed(cfg, tmp_path) is False


def test_whisper_installed_without_package(cfg, tmp_path):
    # faster-whisper может быть ещё не установлен — в этом случае False.
    assert engine_is_installed(replace(cfg, engine="whisper"), tmp_path) is False


def test_cloud_installed_depends_on_env(cfg, monkeypatch):
    cloud_cfg = replace(cfg, engine="cloud", cloud_api_key_env="GOLOS_TEST_ASR_KEY")
    monkeypatch.delenv("GOLOS_TEST_ASR_KEY", raising=False)
    assert engine_is_installed(cloud_cfg) is False
    monkeypatch.setenv("GOLOS_TEST_ASR_KEY", "secret")
    assert engine_is_installed(cloud_cfg) is True


def test_engine_info_gigaam(cfg, tmp_path):
    info = engine_info(cfg, tmp_path)
    assert info["engine"] == "gigaam"
    assert info["variant"] == "e2e_rnnt"
    assert info["installed"] is False
    assert info["model_dir"].endswith("GigaAM-v3")


def test_engine_info_cloud(cfg, monkeypatch):
    cloud_cfg = replace(cfg, engine="cloud", cloud_api_key_env="GOLOS_TEST_ASR_KEY2")
    monkeypatch.delenv("GOLOS_TEST_ASR_KEY2", raising=False)
    info = engine_info(cloud_cfg)
    assert info["engine"] == "cloud"
    assert info["api_key_set"] is False
    assert info["api_key_env"] == "GOLOS_TEST_ASR_KEY2"


def test_download_model_cloud_without_key_raises(cfg, monkeypatch):
    cloud_cfg = replace(cfg, engine="cloud", cloud_api_key_env="GOLOS_TEST_ASR_KEY3")
    monkeypatch.delenv("GOLOS_TEST_ASR_KEY3", raising=False)
    with pytest.raises(RuntimeError, match="ключ"):
        download_model(cloud_cfg)


def test_download_model_existing_gigaam_does_not_load(cfg, tmp_path):
    _write_model_dir(tmp_path)
    calls: list[tuple[str, float | None]] = []
    download_model(cfg, progress=lambda m, f: calls.append((m, f)), models_dir=tmp_path)
    assert calls == [("Модель уже скачана", 1.0)]
