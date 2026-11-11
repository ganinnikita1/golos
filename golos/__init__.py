"""Golos — офлайн-диктовка для Windows на GigaAM-v3.

Пакет собран так, чтобы каждый слой можно было тестировать отдельно:

    golos.audio    — запись микрофона, VAD, уровни
    golos.asr      — движки распознавания (GigaAM-v3, faster-whisper, облако)
    golos.text     — пунктуация/регистр, словарь, LLM-полировка
    golos.inject   — вставка текста в активное окно
    golos.hotkey   — глобальная горячая клавиша (hold-to-talk / toggle)
    golos.ui       — трей, окно настроек, оверлей, история
    golos.pipeline — оркестрация: аудио → текст → полировка → вставка
"""

__version__ = "0.1.1"
APP_NAME = "Golos"
APP_TITLE = "«Голос» — диктовка для Windows"
