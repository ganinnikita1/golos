"""Тесты форматирования значений и горячих клавиш (без GUI)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from golos.config import default_config
from golos.ui import formatting


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ctrl+alt+space", "Ctrl+Alt+Space"),
        ("f9", "F9"),
        ("ctrl+shift+d", "Ctrl+Shift+D"),
        ("right alt", "Right Alt"),
        ("escape", "Esc"),
        ("", "Не задана"),
        (None, "Не задана"),
    ],
)
def test_format_hotkey(spec, expected):
    assert formatting.format_hotkey(spec) == expected


@pytest.mark.parametrize(
    ("spec", "ok"),
    [
        ("ctrl+alt+space", True),
        ("f9", True),
        ("shift+enter", True),
        ("", False),
        ("space", False),
        ("a", False),
    ],
)
def test_validate_hotkey_spec(spec, ok):
    assert (formatting.validate_hotkey_spec(spec) is None) is ok


def test_validate_cancel_key():
    assert formatting.validate_cancel_key("esc") is None
    assert formatting.validate_cancel_key("ctrl+esc") is not None
    assert formatting.validate_cancel_key("") is not None


def test_key_sequence_to_spec():
    from PySide6.QtGui import QKeySequence

    assert formatting.key_sequence_to_spec(QKeySequence("Ctrl+Alt+Space")) == "ctrl+alt+space"
    assert formatting.key_sequence_to_spec(QKeySequence("F9")) == "f9"
    assert formatting.key_sequence_to_spec(QKeySequence("Meta+S")) == "win+s"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0:00"), (5.9, "0:05"), (65, "1:05"), (3661, "1:01:01")],
)
def test_format_elapsed(seconds, expected):
    assert formatting.format_elapsed(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "0,0 с"), (0.8, "0,8 с"), (12.4, "12 с"), (65, "1 мин 05 с")],
)
def test_format_duration(seconds, expected):
    assert formatting.format_duration(seconds) == expected


def test_format_rtf():
    assert formatting.format_rtf(0.123) == "0,12"
    assert formatting.format_rtf(0) == "0,00"


def test_plural_and_labels():
    assert formatting.records_label(1) == "1 запись"
    assert formatting.records_label(2) == "2 записи"
    assert formatting.records_label(5) == "5 записей"
    assert formatting.minutes_label(65) == "2 минуты"
    assert formatting.minutes_label(0) == "0 минут"


def test_format_timestamp():
    now = datetime.now()
    assert formatting.format_timestamp(now.timestamp()).startswith("Сегодня,")
    yesterday = now - timedelta(days=1)
    assert formatting.format_timestamp(yesterday.timestamp()).startswith("Вчера,")
    old = now - timedelta(days=40)
    formatted = formatting.format_timestamp(old.timestamp())
    assert ":" in formatted and formatted[0].isdigit()
    assert formatting.format_timestamp("плохое значение") == "—"


def test_model_title_and_env_status():
    cfg = default_config()
    assert formatting.model_title(cfg) == "GigaAM-v3 · e2e_rnnt"
    cfg.asr.engine = "whisper"
    assert formatting.model_title(cfg) == "faster-whisper · large-v3-turbo"
    cfg.asr.engine = "cloud"
    assert formatting.model_title(cfg).startswith("Облако ·")
    assert "не задана" in formatting.env_status_text("OPENAI_API_KEY", False)
    assert "найден" in formatting.env_status_text("OPENAI_API_KEY", True)


def test_engine_status_text():
    assert "не загружена" in formatting.engine_status_text(
        {"engine": "gigaam", "loaded": False, "loading": False, "message": ""}
    )
    assert "загрузка" in formatting.engine_status_text(
        {"engine": "gigaam", "loaded": False, "loading": True, "message": ""}
    )
    assert "загружена" in formatting.engine_status_text(
        {"engine": "gigaam", "loaded": True, "loading": False, "message": ""}
    )
