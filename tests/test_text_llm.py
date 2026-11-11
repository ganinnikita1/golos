"""Тесты LLM-полировки: конфигурация, HTTP, fallback-поведение."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from golos.config import PolishConfig
from golos.text import llm as llm_module
from golos.text.llm import LLMPolisher


class FakeResponse(io.BytesIO):
    """Минимальный ответ, совместимый с ``with urlopen(...) as r``."""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def make_cfg(**overrides) -> PolishConfig:
    cfg = PolishConfig(enabled=True, base_url="https://api.deepseek.com/v1",
                       model="deepseek-chat", api_key_env="GOLOS_TEST_LLM_KEY", timeout_s=1.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def install_fake_urlopen(monkeypatch, *, content=None, response=None, error=None) -> dict:
    """Подменяет urlopen; возвращает словарь, куда запишется запрос."""
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        if error is not None:
            raise error
        if response is not None:
            return FakeResponse(response)
        payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    return captured


@pytest.fixture(autouse=True)
def _clean_key(monkeypatch):
    monkeypatch.delenv("GOLOS_TEST_LLM_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# is_configured
# --------------------------------------------------------------------------- #
def test_not_configured_without_key(monkeypatch) -> None:
    assert LLMPolisher(make_cfg()).is_configured() is False


def test_not_configured_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    assert LLMPolisher(make_cfg(enabled=False)).is_configured() is False


def test_configured_with_key(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    assert LLMPolisher(make_cfg()).is_configured() is True


def test_empty_api_key_env_is_not_configured(monkeypatch) -> None:
    assert LLMPolisher(make_cfg(api_key_env="")).is_configured() is False


# --------------------------------------------------------------------------- #
# Fallback без сети
# --------------------------------------------------------------------------- #
def test_polish_returns_raw_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    captured = install_fake_urlopen(monkeypatch, content="НОВЫЙ ТЕКСТ")
    polisher = LLMPolisher(make_cfg(enabled=False))
    assert polisher.polish("исходный текст") == "исходный текст"
    assert captured == {}


def test_polish_returns_raw_without_key(monkeypatch) -> None:
    captured = install_fake_urlopen(monkeypatch, content="НОВЫЙ ТЕКСТ")
    assert LLMPolisher(make_cfg()).polish("исходный текст") == "исходный текст"
    assert captured == {}


def test_polish_empty_text_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    assert LLMPolisher(make_cfg()).polish("") == ""
    assert LLMPolisher(make_cfg()).polish("   ") == "   "


# --------------------------------------------------------------------------- #
# Успешный запрос
# --------------------------------------------------------------------------- #
def test_polish_success_and_request_shape(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    captured = install_fake_urlopen(monkeypatch, content="Привет, мир!")
    result = LLMPolisher(make_cfg()).polish("привет мир", context="Блокнот")
    assert result == "Привет, мир!"

    request = captured["request"]
    assert request.full_url == "https://api.deepseek.com/v1/chat/completions"
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer sk-test"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "deepseek-chat"
    assert payload["stream"] is False
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert "Блокнот" in payload["messages"][1]["content"]
    assert "привет мир" in payload["messages"][1]["content"]
    assert captured["timeout"] == pytest.approx(1.0)


def test_polish_literal_style_uses_other_prompt(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    captured = install_fake_urlopen(monkeypatch, content="Привет.")
    LLMPolisher(make_cfg(style="literal")).polish("привет")
    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert "только пунктуацию" in payload["messages"][0]["content"]


def test_polish_strips_model_wrapping(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    for wrapped in ('"Привет, мир."', "```text\nПривет, мир.\n```", "«Привет, мир.»"):
        install_fake_urlopen(monkeypatch, content=wrapped)
        assert LLMPolisher(make_cfg()).polish("привет мир") == "Привет, мир."


# --------------------------------------------------------------------------- #
# Ошибки и защита
# --------------------------------------------------------------------------- #
def test_polish_falls_back_on_timeout(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    install_fake_urlopen(monkeypatch, error=TimeoutError("timed out"))
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_polish_falls_back_on_http_error(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    error = urllib.error.HTTPError("https://x", 401, "unauthorized", hdrs=None, fp=None)
    install_fake_urlopen(monkeypatch, error=error)
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_polish_falls_back_on_broken_json(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    install_fake_urlopen(monkeypatch, response=b"{not json")
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_polish_falls_back_on_empty_choices(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    install_fake_urlopen(monkeypatch, response=json.dumps({"choices": []}).encode())
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_polish_falls_back_on_missing_content(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    payload = {"choices": [{"message": {"role": "assistant"}}]}
    install_fake_urlopen(monkeypatch, response=json.dumps(payload).encode())
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_polish_falls_back_on_suspicious_length(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    install_fake_urlopen(monkeypatch, content="совершенно другой огромный текст " * 20)
    assert LLMPolisher(make_cfg()).polish("привет мир") == "привет мир"


def test_api_key_is_not_logged(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-secret-key-12345")
    import logging

    caplog.set_level(logging.DEBUG)
    install_fake_urlopen(monkeypatch, content="Привет.")
    LLMPolisher(make_cfg()).polish("привет")
    install_fake_urlopen(monkeypatch, error=TimeoutError("boom"))
    LLMPolisher(make_cfg()).polish("привет")
    assert "sk-secret-key-12345" not in caplog.text


def test_update_config(monkeypatch) -> None:
    monkeypatch.setenv("GOLOS_TEST_LLM_KEY", "sk-test")
    polisher = LLMPolisher(make_cfg())
    assert polisher.is_configured() is True
    polisher.update_config(make_cfg(enabled=False))
    assert polisher.is_configured() is False
