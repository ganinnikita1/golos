"""Облачный ASR: любой OpenAI-совместимый ``POST /audio/transcriptions``.

Ключ берётся только из переменной окружения (``cfg.cloud_api_key_env``),
в конфиг и логи он не попадает. Без ключа движок не падает при импорте/создании,
но загрузка и распознавание выдают понятную русскую ошибку.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np

from ..config import ASRConfig
from .audio_utils import DEFAULT_SR, encode_wav, resample, to_mono_float32
from .base import ProgressFn, Transcript

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0


class CloudEngine:
    """OpenAI-совместимый облачный движок (openai / groq / custom)."""

    name = "cloud"

    def __init__(self, cfg: ASRConfig, models_dir: Path | str | None = None) -> None:
        self.cfg = cfg
        self._models_dir = Path(models_dir) if models_dir is not None else None
        self._loaded = False

    # ------------------------------------------------------------------ #
    def api_key(self) -> str | None:
        env_name = self.cfg.cloud_api_key_env or "OPENAI_API_KEY"
        value = os.environ.get(env_name)
        return value.strip() if value and value.strip() else None

    @property
    def url(self) -> str:
        base = (self.cfg.cloud_base_url or "").strip().rstrip("/")
        from ..net_safety import validate_endpoint

        try:
            base = validate_endpoint(base, purpose="облачного распознавания")
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if not base:
            base = "https://api.openai.com/v1"
        return f"{base}/audio/transcriptions"

    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------ #
    def load(self, progress: ProgressFn | None = None) -> None:
        key = self.api_key()
        if key is None:
            env_name = self.cfg.cloud_api_key_env or "OPENAI_API_KEY"
            raise RuntimeError(
                f"Не задан API-ключ облачного распознавания. "
                f"Установите переменную окружения {env_name} и перезапустите приложение."
            )
        self._loaded = True
        if progress is not None:
            try:
                progress("Облачный движок готов", 1.0)
            except Exception:  # pragma: no cover
                pass

    def unload(self) -> None:
        self._loaded = False

    # ------------------------------------------------------------------ #
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        key = self.api_key()
        if key is None:
            env_name = self.cfg.cloud_api_key_env or "OPENAI_API_KEY"
            raise RuntimeError(
                f"Не задан API-ключ облачного распознавания: переменная {env_name}."
            )

        wav = resample(to_mono_float32(audio), int(sample_rate or DEFAULT_SR), DEFAULT_SR)
        duration = float(wav.size) / DEFAULT_SR
        if wav.size == 0:
            return Transcript(text="", engine=self.name, duration_s=0.0, elapsed_s=0.0, raw="")
        wav_bytes = encode_wav(wav, DEFAULT_SR)

        fields = {
            "model": self.cfg.cloud_model or "gpt-4o-transcribe",
            "language": self.cfg.language or "ru",
            "response_format": "json",
        }
        boundary, body = build_multipart(fields, "file", "audio.wav", wav_bytes)

        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Golos/0.1 (+asr)",
            },
        )
        timeout = float(getattr(self.cfg, "cloud_timeout_s", 0) or _DEFAULT_TIMEOUT_S)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:  # pragma: no cover
                pass
            raise RuntimeError(
                f"Облачное распознавание вернуло ошибку HTTP {exc.code}"
                + (f": {detail}" if detail else "")
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Нет связи с облачным распознаванием ({self.url}): {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Облачное распознавание не ответило за {timeout:.0f} с"
            ) from exc

        text = parse_transcription_response(payload)
        elapsed = time.perf_counter() - started
        return Transcript(
            text=text,
            engine=self.name,
            language=self.cfg.language or "ru",
            duration_s=duration,
            elapsed_s=elapsed,
            raw=text,
        )


def build_multipart(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "audio/wav",
) -> tuple[str, bytes]:
    """Собирает multipart/form-data тело. Возвращает (boundary, body)."""
    boundary = f"----Golos{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def parse_transcription_response(payload: str) -> str:
    """Достаёт текст из ответа: JSON ``{"text": ...}`` или plain text."""
    payload = (payload or "").strip()
    if not payload:
        return ""
    try:
        data = json.loads(payload)
    except ValueError:
        return payload
    if isinstance(data, dict):
        text = data.get("text")
        if isinstance(text, str):
            return text.strip()
        # некоторые провайдеры отдают {"results": [{"alternatives": [{"transcript": ...}]}]}
        results = data.get("results")
        if isinstance(results, list) and results:
            item = results[0]
            if isinstance(item, dict):
                alternatives = item.get("alternatives")
                if isinstance(alternatives, list) and alternatives:
                    transcript = alternatives[0].get("transcript")
                    if isinstance(transcript, str):
                        return transcript.strip()
    return ""
