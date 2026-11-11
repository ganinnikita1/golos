"""Тесты конфигурации: дефолты, частичный JSON, битые значения, атомарная запись."""

from __future__ import annotations

import json

from golos.config import (
    AppConfig,
    AudioConfig,
    TextConfig,
    default_config,
    load_config,
    save_config,
    update_config,
)


def test_defaults_are_sane() -> None:
    cfg = default_config()
    assert cfg.asr.engine == "gigaam"
    assert cfg.asr.gigaam_variant == "e2e_rnnt"
    assert cfg.audio.sample_rate == 16000
    assert cfg.hotkey.hotkey == "ctrl+alt+space"
    assert cfg.hotkey.mode == "toggle"
    assert cfg.insert.method == "type"
    assert cfg.polish.enabled is False
    assert cfg.onboarding_done is False


def test_load_missing_file_returns_defaults(tmp_path) -> None:
    cfg = load_config(tmp_path / "nope.json")
    assert isinstance(cfg, AppConfig)
    assert cfg.version == default_config().version


def test_load_partial_json_keeps_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"asr": {"engine": "whisper"}, "hotkey": {"mode": "push_to_talk"}}),
                    encoding="utf-8")
    cfg = load_config(path)
    assert cfg.asr.engine == "whisper"
    assert cfg.asr.gigaam_variant == "e2e_rnnt"  # дефолт сохранился
    assert cfg.hotkey.mode == "push_to_talk"
    assert cfg.hotkey.hotkey == "ctrl+alt+space"


def test_load_broken_json_returns_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path).asr.engine == "gigaam"


def test_validate_fixes_unknown_values(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"asr": {"engine": "skynet", "gigaam_variant": "x"},
                                "audio": {"vad": "nope", "sample_rate": 12345},
                                "insert": {"method": "telepathy"},
                                "hotkey": {"mode": "??"}}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.asr.engine == "gigaam"
    assert cfg.asr.gigaam_variant == "e2e_rnnt"
    assert cfg.audio.vad == "energy"
    assert cfg.audio.sample_rate == 16000
    assert cfg.insert.method == "type"
    assert cfg.hotkey.mode == "toggle"


def test_save_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg.text.dictionary = []
    cfg.asr.whisper_model = "small"
    cfg.ui.overlay_enabled = False
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.asr.whisper_model == "small"
    assert loaded.ui.overlay_enabled is False


def test_dictionary_survives_roundtrip(tmp_path) -> None:
    from golos.config import DictionaryEntry

    path = tmp_path / "config.json"
    cfg = default_config()
    cfg.text.dictionary = [DictionaryEntry(source="джипити", target="GPT")]
    save_config(cfg, path)
    loaded = load_config(path)
    assert len(loaded.text.dictionary) == 1
    assert loaded.text.dictionary[0].source == "джипити"
    assert loaded.text.dictionary[0].target == "GPT"


def test_update_config_does_not_mutate_original() -> None:
    cfg = default_config()
    updated = update_config(cfg, audio={"vad": "off"})
    assert updated.audio.vad == "off"
    assert cfg.audio.vad == "energy"
    assert isinstance(updated.text, TextConfig)
    assert isinstance(updated.audio, AudioConfig)


def test_unknown_keys_ignored(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"quantum_flux": 42, "audio": {"loudness": 11}}), encoding="utf-8")
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
