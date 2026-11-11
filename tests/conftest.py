"""Общие фикстуры pytest для «Голоса».

ВАЖНО: тесты должны быть безопасны и не требовать GUI/микрофона по умолчанию.
Тяжёлые проверки (реальная модель GigaAM) помечаются маркером `slow`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Изолируем конфиг/историю/модели внутри workspace (в ограниченном окружении (песочнице) другие пути недоступны).
_TEST_DATA = ROOT / ".devdata"
_HF = _TEST_DATA / "hf"
os.environ.setdefault("GOLOS_APPDATA", str(_TEST_DATA / "appdata"))
os.environ.setdefault("GOLOS_LOCALAPPDATA", str(_TEST_DATA / "localappdata"))
os.environ.setdefault("HF_HOME", str(_HF))
os.environ.setdefault("HF_HUB_CACHE", str(_HF / "hub"))
for _path in (
    _TEST_DATA / "appdata",
    _TEST_DATA / "localappdata",
    _TEST_DATA / "appdata" / "logs",
    _TEST_DATA / "localappdata" / "models",
    _TEST_DATA / "localappdata" / "cache",
    _HF / "hub",
):
    _path.mkdir(parents=True, exist_ok=True)


FIXTURES = ROOT / "tests" / "fixtures"
RU_TTS = FIXTURES / "ru_tts"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: требует загруженную модель/долгие вычисления")
    config.addinivalue_line("markers", "gui: требует графическую сессию")
    config.addinivalue_line("markers", "win32: использует реальные Win32-вызовы")


@pytest.fixture()
def app_config():
    from golos.config import default_config

    return default_config()


@pytest.fixture()
def ru_manifest() -> list[dict]:
    """Манифест синтезированного русского набора (tests/fixtures/ru_tts/manifest.json)."""
    path = RU_TTS / "manifest.json"
    if not path.exists():
        pytest.skip("нет набора tests/fixtures/ru_tts (запусти tests/fixtures/make_tts.ps1)")
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.fixture()
def sine_audio() -> tuple:
    """1 секунда синуса 440 Гц, float32 mono 16 кГц."""
    import numpy as np

    sr = 16000
    t = np.arange(sr, dtype=np.float32) / sr
    return (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32), sr
