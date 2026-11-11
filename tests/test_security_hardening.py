"""Тесты безопасности: валидация endpoint'ов и путей конфига."""

from __future__ import annotations

import pytest

from golos.config import default_config, load_config
from golos.net_safety import UnsafeEndpointError, validate_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://api.deepseek.com/v1",
        "http://localhost:1234/v1",
        "http://127.0.0.1:8080/v1",
    ],
)
def test_validate_endpoint_allows_safe(url: str) -> None:
    assert validate_endpoint(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1",
        "http://example.com",
        "ftp://example.com",
        "file:///etc/passwd",
        "",
        "no-scheme",
    ],
)
def test_validate_endpoint_rejects_unsafe(url: str) -> None:
    with pytest.raises(UnsafeEndpointError):
        validate_endpoint(url)


def test_llm_endpoint_problem_reports_http() -> None:
    from golos.text.llm import LLMPolisher

    cfg = default_config()
    cfg.polish.enabled = True
    cfg.polish.base_url = "http://insecure.example/v1"
    polisher = LLMPolisher(cfg.polish)
    assert polisher.endpoint_problem()


def test_config_rejects_path_traversal_in_model_dir(tmp_path) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"asr": {"gigaam_dirname": "..\\..\\evil", "gigaam_repo": "../../etc"}}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.asr.gigaam_dirname == "GigaAM-v3"
    assert cfg.asr.gigaam_repo == "ai-sage/GigaAM-v3"


def test_config_rejects_absolute_dir(tmp_path) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"asr": {"gigaam_dirname": "C:\\Windows"}}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.asr.gigaam_dirname == "GigaAM-v3"


def test_default_config_rejects_unverified_models() -> None:
    cfg = default_config()
    assert cfg.asr.allow_unverified_model is False
    assert cfg.asr.verify_model_hashes is True


def test_history_can_be_disabled() -> None:
    cfg = default_config()
    assert cfg.history_enabled is True
