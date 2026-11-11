"""Тесты целостности модели и приватности путей в UI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from golos import paths
from golos.asr import integrity
from golos.config import default_config


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def fake_manifest(tmp_path, monkeypatch):
    """Подменяем манифест маленьким тестовым набором."""
    good = (tmp_path / "model" / "config.json")
    good.parent.mkdir(parents=True)
    good.write_bytes(b'{"model": "test"}')
    payload = {
        "schema": 1,
        "repo": "test/repo",
        "variants": {
            "e2e_rnnt": {
                "commit": "a" * 40,
                "files": {
                    "config.json": {"sha256": _sha256(good.read_bytes()), "size": good.stat().st_size}
                },
            }
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(integrity, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(integrity, "_MANIFEST", None)
    monkeypatch.setattr(integrity, "_MANIFEST_LOADED", False)
    return good.parent


def test_expectation_loaded(fake_manifest) -> None:
    expectation = integrity.variant_expectation("test/repo", "e2e_rnnt")
    assert expectation is not None
    assert expectation.commit == "a" * 40
    assert "config.json" in expectation.files


def test_unknown_repo_or_variant(fake_manifest) -> None:
    assert integrity.variant_expectation("other/repo", "e2e_rnnt") is None
    assert integrity.variant_expectation("test/repo", "ctc") is None


def test_verify_ok(fake_manifest) -> None:
    assert integrity.verify_model_dir(fake_manifest, "test/repo", "e2e_rnnt") == []


def test_verify_detects_tampering(fake_manifest) -> None:
    (fake_manifest / "config.json").write_bytes(b'{"model": "tampered"}')
    problems = integrity.verify_model_dir(fake_manifest, "test/repo", "e2e_rnnt")
    assert problems
    assert "SHA256" in problems[0] or "размер" in problems[0]


def test_verify_detects_missing(fake_manifest) -> None:
    (fake_manifest / "config.json").unlink()
    problems = integrity.verify_model_dir(fake_manifest, "test/repo", "e2e_rnnt")
    assert any("отсутствует" in problem for problem in problems)


def test_verify_skips_unknown_variant(fake_manifest) -> None:
    assert integrity.verify_model_dir(fake_manifest, "test/repo", "unknown") == []


def test_real_manifest_has_default_variant() -> None:
    """В репозитории есть манифест с проверенными хешами для дефолтной модели."""
    data = integrity.manifest()
    assert data is not None, "model_manifest.json должен поставляться с приложением"
    assert "e2e_rnnt" in data.get("variants", {})
    entry = data["variants"]["e2e_rnnt"]
    assert len(entry["commit"]) == 40
    assert "pytorch_model.bin" in entry["files"]


def test_default_config_verifies_model() -> None:
    cfg = default_config()
    assert cfg.asr.verify_model_hashes is True
    assert cfg.asr.pin_model_revision is True


# --------------------------------------------------------------------------- #
# Приватность путей в UI и диагностике
# --------------------------------------------------------------------------- #
def test_display_path_hides_username() -> None:
    shown = paths.display_path(paths.appdata_dir())
    assert "\\Users\\" not in shown
    assert "<user>" not in shown
    assert shown.startswith("%APPDATA%") or shown.startswith("%USERPROFILE%")


def test_display_path_models() -> None:
    shown = paths.display_path(paths.models_dir())
    assert "\\Users\\" not in shown
    assert shown.startswith("%LOCALAPPDATA%") or shown.startswith("%USERPROFILE%")


def test_sanitize_for_report() -> None:
    fake_home = str(paths._user_home())
    text = f"config: {fake_home}\\AppData\\Roaming\\Golos\\config.json"
    cleaned = paths.sanitize_for_report(text)
    assert fake_home not in cleaned
    assert "%USERPROFILE%" in cleaned
