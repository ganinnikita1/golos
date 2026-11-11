"""LLM-полировка диктовки через OpenAI-совместимый ``chat/completions``.

Провайдер по умолчанию — DeepSeek (``https://api.deepseek.com/v1``,
``deepseek-chat``), но подходит любой совместимый endpoint. Ключ берётся
только из переменной окружения, имя которой задано в ``cfg.api_key_env``
(по умолчанию ``DEEPSEEK_API_KEY``); в коде и логах ключа нет.

Полировка — необязательный шаг: любая ошибка, таймаут или подозрительный
ответ возвращают исходный текст, пайплайн не должен из-за неё падать.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

from ..config import PolishConfig

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS: dict[str, str] = {
    "dictation": (
        "Ты — корректор русской диктовки. Тебе дают текст, распознанный с голоса. "
        "Расставь пунктуацию и заглавные буквы, исправь явные ошибки распознавания "
        "и убери слова-паразиты. Сохрани стиль, смысл и порядок слов: ничего не "
        "переписывай и не добавляй от себя. Не трогай аббревиатуры (РФ, ИТ, GPT, API), "
        "числа и имена. Верни ТОЛЬКО исправленный текст — без пояснений, кавычек "
        "и markdown-разметки."
    ),
    "literal": (
        "Ты — корректор русской диктовки. Исправь только пунктуацию и заглавные "
        "буквы, максимально сохранив исходные слова. Ничего не перефразируй и не "
        "добавляй. Верни ТОЛЬКО исправленный текст — без пояснений и markdown."
    ),
}

_USER_TEMPLATE = "Текст диктовки:\n\n{text}\n\nВерни только исправленный текст."

_WRAP_CHARS = ('"', "'", "«", "»", "„", "“", "”", "`")


def _strip_model_wrapping(text: str) -> str:
    """Убирает типичные «обёртки» модели: кавычки, markdown-блоки."""
    result = text.strip()
    if result.startswith("```"):
        result = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", result)
        result = re.sub(r"\n?```\s*$", "", result)
        result = result.strip()
    if len(result) >= 2 and result[0] in _WRAP_CHARS and result[-1] in _WRAP_CHARS:
        result = result[1:-1].strip()
    return result


class LLMPolisher:
    """Клиент полировки. Удовлетворяет протоколу ``golos.text.base.Polisher``."""

    def __init__(self, cfg: PolishConfig | None = None) -> None:
        self._cfg = cfg or PolishConfig()

    # ------------------------------------------------------------------ #
    # Конфигурация
    # ------------------------------------------------------------------ #
    @property
    def cfg(self) -> PolishConfig:
        return self._cfg

    def update_config(self, cfg: PolishConfig) -> None:
        """Горячая замена настроек (UI вызывает после «Сохранить»)."""
        self._cfg = cfg

    def api_key(self) -> str | None:
        """Ключ из переменной окружения; значение никогда не логируется."""
        env_name = (self._cfg.api_key_env or "").strip()
        if not env_name:
            return None
        value = os.environ.get(env_name, "").strip()
        return value or None

    def is_configured(self) -> bool:
        """Можно ли вообще пробовать: полировка включена, есть URL и ключ."""
        return bool(
            self._cfg.enabled
            and (self._cfg.base_url or "").strip()
            and (self._cfg.model or "").strip()
            and self.api_key()
        )

    def endpoint_problem(self) -> str | None:
        """Проверка адреса без сетевого запроса (для UI/диагностики)."""
        try:
            from ..net_safety import validate_endpoint

            validate_endpoint(self._cfg.base_url, purpose="LLM-полировки")
        except ValueError as exc:
            return str(exc)
        return None

    # ------------------------------------------------------------------ #
    # Полировка
    # ------------------------------------------------------------------ #
    def polish(self, text: str, *, context: str | None = None) -> str:
        """Возвращает улучшенный текст или исходный при любой проблеме."""
        if not isinstance(text, str) or not text.strip():
            return text if isinstance(text, str) else ""
        original = text
        cfg = self._cfg
        if not cfg.enabled:
            return original
        key = self.api_key()
        if not key:
            logger.debug("LLM-полировка пропущена: переменная %s не задана", cfg.api_key_env)
            return original
        base_url = (cfg.base_url or "").strip().rstrip("/")
        if not base_url:
            return original
        try:
            from ..net_safety import validate_endpoint

            base_url = validate_endpoint(base_url, purpose="LLM-полировки")
            result = self._request(base_url, key, text, context)
        except Exception as exc:  # небезопасный URL, timeout, сеть, битый ответ — fallback
            logger.warning("LLM-полировка не удалась (%s: %s), оставляю исходный текст",
                           type(exc).__name__, exc)
            return original if cfg.fallback_to_raw else original
        if not isinstance(result, str) or not result.strip():
            return original
        cleaned = _strip_model_wrapping(result)
        if not cleaned:
            return original
        # Защита от «переписывания»: слишком сильное изменение длины — отказ.
        source_len = len(text.strip())
        if source_len >= 8 and not (0.4 * source_len <= len(cleaned) <= 3.0 * source_len):
            logger.warning("LLM вернула текст подозрительной длины (%d → %d), оставляю исходный",
                           source_len, len(cleaned))
            return original
        return cleaned

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _build_payload(self, text: str, context: str | None) -> dict:
        cfg = self._cfg
        style = cfg.style if cfg.style in _SYSTEM_PROMPTS else "dictation"
        user_text = text.strip()
        if context:
            user_text = f"(Контекст: {str(context)[:200]})\n\n{user_text}"
        return {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPTS[style]},
                {"role": "user", "content": _USER_TEMPLATE.format(text=user_text)},
            ],
            "temperature": 0.2,
            "stream": False,
        }

    def _request(self, base_url: str, key: str, text: str, context: str | None) -> str:
        payload = json.dumps(self._build_payload(text, context), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            base_url + "/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "Golos/0.1 (Windows dictation app)",
            },
        )
        timeout = max(0.5, float(self._cfg.timeout_s or 8.0))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        data = json.loads(body.decode("utf-8", errors="replace"))
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("в ответе LLM нет choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("в ответе LLM нет текста")
        return content
