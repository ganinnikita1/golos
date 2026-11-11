"""Интеграционный тест GigaAM-v3: настоящая модель, если она уже скачана.

В ограниченном окружении (песочнице) каталоги %APPDATA% недоступны на запись, поэтому применяем
``dev_env`` (переводит пути в ``repos/golos/.devdata``). Если модели в
ожидаемом каталоге нет — тесты аккуратно пропускаются.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # в прод-окружении dev_env отсутствует — не мешаем
    from dev_env import apply as _apply_dev_env

    _apply_dev_env()
except Exception:  # pragma: no cover
    pass

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from golos.asr.gigaam_engine import gigaam_model_installed  # noqa: E402
from golos.asr.registry import create_engine  # noqa: E402
from golos.config import default_config  # noqa: E402

_MODEL_READY = gigaam_model_installed(default_config().asr)
pytestmark = pytest.mark.skipif(
    not _MODEL_READY,
    reason="Модель GigaAM-v3 e2e_rnnt не скачана (нет в paths.models_dir())",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(default_config().asr)
    eng.load()
    try:
        yield eng
    finally:
        eng.unload()


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data, dtype=np.float32), int(sr)
    except ImportError:  # pragma: no cover
        import wave

        with wave.open(str(path), "rb") as wav:
            sr = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0, sr


def test_model_loads_and_transcribes_silence(engine):
    from golos.asr.audio_utils import DEFAULT_SR

    assert engine.is_loaded()
    transcript = engine.transcribe(np.zeros(DEFAULT_SR, dtype=np.float32), DEFAULT_SR)
    assert transcript.engine == "gigaam"
    assert abs(transcript.duration_s - 1.0) < 0.05
    assert transcript.elapsed_s > 0.0
    assert isinstance(transcript.text, str)


def test_fixture_transcription_has_punctuation_and_capitals(engine):
    wav_path = ROOT / "tests" / "fixtures" / "ru_tts" / "01.wav"
    if not wav_path.is_file():
        pytest.skip("Нет WAV-фикстуры для интеграционного теста")
    audio, sr = _read_wav(wav_path)
    transcript = engine.transcribe(audio, sr)
    assert transcript.text.strip(), "модель вернула пустой текст на чистой фразе"
    assert transcript.text[:1].isupper(), "e2e-модель должна ставить заглавную"
    assert transcript.text.rstrip()[-1] in ".!?…", "e2e-модель должна ставить знак в конце"
    assert transcript.duration_s > 3.0
    # очень мягкий потолок: даже на загруженной машине не должно быть медленнее
    # реального времени
    assert transcript.rtf < 1.5
