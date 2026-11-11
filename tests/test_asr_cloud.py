"""Тесты облачного движка: multipart, разбор ответа, ошибки без ключа."""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace

import numpy as np
import pytest

from golos.asr import cloud_engine
from golos.asr.cloud_engine import (
    CloudEngine,
    build_multipart,
    parse_transcription_response,
)
from golos.config import default_config


@pytest.fixture()
def cfg():
    return replace(
        default_config().asr,
        engine="cloud",
        cloud_api_key_env="GOLOS_TEST_CLOUD_KEY",
        cloud_base_url="https://asr.example.test/v1",
        cloud_model="test-model",
    )


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_build_multipart_structure():
    body = build_multipart(
        {"model": "m", "language": "ru"}, "file", "audio.wav", b"RIFFDATA"
    )
    boundary, data = body
    assert boundary.startswith("----Golos")
    assert data.startswith(f"--{boundary}\r\n".encode())
    assert data.rstrip().endswith(f"--{boundary}--".encode())
    assert b'name="model"\r\n\r\nm\r\n' in data
    assert b'name="language"\r\n\r\nru\r\n' in data
    assert b'filename="audio.wav"' in data
    assert b"Content-Type: audio/wav" in data
    assert b"RIFFDATA" in data


def test_parse_transcription_json():
    assert parse_transcription_response('{"text": " Привет, мир. "}') == "Привет, мир."


def test_parse_transcription_plain_text():
    assert parse_transcription_response("просто текст") == "просто текст"


def test_parse_transcription_empty():
    assert parse_transcription_response("") == ""
    assert parse_transcription_response("{}") == ""


def test_parse_transcription_alternatives():
    payload = json.dumps(
        {"results": [{"alternatives": [{"transcript": "тест"}]}]}
    )
    assert parse_transcription_response(payload) == "тест"


def test_url_and_key(cfg, monkeypatch):
    engine = CloudEngine(cfg)
    assert engine.url == "https://asr.example.test/v1/audio/transcriptions"
    monkeypatch.delenv("GOLOS_TEST_CLOUD_KEY", raising=False)
    assert engine.api_key() is None
    monkeypatch.setenv("GOLOS_TEST_CLOUD_KEY", "  abc  ")
    assert engine.api_key() == "abc"


def test_load_without_key_raises(cfg, monkeypatch):
    monkeypatch.delenv("GOLOS_TEST_CLOUD_KEY", raising=False)
    engine = CloudEngine(cfg)
    with pytest.raises(RuntimeError, match="GOLOS_TEST_CLOUD_KEY"):
        engine.load()


def test_transcribe_without_key_raises(cfg, monkeypatch):
    monkeypatch.delenv("GOLOS_TEST_CLOUD_KEY", raising=False)
    engine = CloudEngine(cfg)
    with pytest.raises(RuntimeError, match="ключ"):
        engine.transcribe(np.zeros(16000, dtype=np.float32), 16000)


def test_transcribe_posts_multipart_and_parses(cfg, monkeypatch):
    monkeypatch.setenv("GOLOS_TEST_CLOUD_KEY", "secret-key")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _FakeResponse('{"text": "Привет, мир."}'.encode("utf-8"))

    monkeypatch.setattr(cloud_engine.urllib.request, "urlopen", fake_urlopen)
    engine = CloudEngine(cfg)
    engine.load()
    audio = np.zeros(16000, dtype=np.float32)
    transcript = engine.transcribe(audio, 16000)

    assert captured["url"] == "https://asr.example.test/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert b'name="model"\r\n\r\ntest-model' in captured["body"]
    assert b'name="language"\r\n\r\nru' in captured["body"]
    assert captured["body"].startswith(b"------Golos")
    assert transcript.text == "Привет, мир."
    assert transcript.engine == "cloud"
    assert abs(transcript.duration_s - 1.0) < 0.01
    assert transcript.elapsed_s >= 0.0
    assert transcript.rtf >= 0.0


def test_transcribe_http_error_message(cfg, monkeypatch):
    monkeypatch.setenv("GOLOS_TEST_CLOUD_KEY", "bad")
    error = urllib.error.HTTPError(
        "https://asr.example.test/v1/audio/transcriptions",
        401,
        "Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b'{"error": "invalid key"}'),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(cloud_engine.urllib.request, "urlopen", fake_urlopen)
    engine = CloudEngine(cfg)
    with pytest.raises(RuntimeError, match="401"):
        engine.transcribe(np.zeros(16000, dtype=np.float32), 16000)


def test_transcribe_network_error_message(cfg, monkeypatch):
    monkeypatch.setenv("GOLOS_TEST_CLOUD_KEY", "key")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("нет сети")

    monkeypatch.setattr(cloud_engine.urllib.request, "urlopen", fake_urlopen)
    engine = CloudEngine(cfg)
    with pytest.raises(RuntimeError, match="Нет связи"):
        engine.transcribe(np.zeros(16000, dtype=np.float32), 16000)


def test_empty_audio_returns_empty(cfg, monkeypatch):
    monkeypatch.setenv("GOLOS_TEST_CLOUD_KEY", "key")
    engine = CloudEngine(cfg)
    transcript = engine.transcribe(np.zeros(0, dtype=np.float32), 16000)
    assert transcript.text == ""
    assert transcript.duration_s == 0.0
