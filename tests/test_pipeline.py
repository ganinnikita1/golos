"""Тесты пайплайна диктовки на фейковом движке (без модели и микрофона)."""

from __future__ import annotations

import numpy as np
import pytest

from golos.asr.base import Transcript
from golos.config import default_config
from golos.events import EventBus
from golos.history import HistoryStore
from golos.pipeline import DictationPipeline, PipelineHooks, SessionState


class FakeEngine:
    name = "fake"

    def __init__(self, text: str = "привет это тест", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0
        self._loaded = False

    def load(self, progress=None) -> None:
        self._loaded = True
        if progress:
            progress("загрузка", 1.0)

    def unload(self) -> None:
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def transcribe(self, audio, sample_rate: int) -> Transcript:
        self.calls += 1
        if self.fail:
            raise RuntimeError("ASR сломался")
        duration = audio.shape[0] / float(sample_rate)
        return Transcript(text=self.text, engine="fake", duration_s=duration, elapsed_s=0.01)


class FakeInjector:
    def __init__(self) -> None:
        self.inserted: list[str] = []

    def inject(self, text: str, cfg) -> bool:  # noqa: ANN001
        self.inserted.append(text)
        return True


@pytest.fixture()
def speech_audio() -> tuple[np.ndarray, int]:
    """2 секунды «речи»: тон с огибающей, чтобы VAD не отрезал всё."""
    sr = 16000
    t = np.arange(sr * 2, dtype=np.float32) / sr
    envelope = np.minimum(1.0, np.minimum(t * 8, (2.0 - t) * 8))
    audio = (0.3 * envelope * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return audio, sr


def make_pipeline(cfg=None, *, text="привет это тест", injector=None, insert=True):
    import tempfile
    from pathlib import Path

    cfg = cfg or default_config()
    bus = EventBus()
    history = HistoryStore(path=Path(tempfile.mkdtemp()) / "history.jsonl", limit=10)
    engine = FakeEngine(text=text)
    injector = injector if injector is not None else FakeInjector()
    pipeline = DictationPipeline(
        cfg, bus, history,
        PipelineHooks(
            engine_provider=lambda: engine,
            injector_factory=lambda method: injector,
            insert=insert,
        ),
    )
    return pipeline, bus, history, engine, injector


def test_full_pipeline_inserts_text(speech_audio) -> None:
    audio, sr = speech_audio
    pipeline, bus, history, engine, injector = make_pipeline()
    result = pipeline.run(audio, sr)
    assert result is not None
    assert result.text
    assert engine.calls == 1
    assert injector.inserted and injector.inserted[0].startswith("Привет")
    assert pipeline.state == SessionState.IDLE
    assert len(history.items()) == 1


def test_result_event_emitted(speech_audio) -> None:
    audio, sr = speech_audio
    pipeline, bus, *_ = make_pipeline()
    pipeline.run(audio, sr)
    events = bus.drain()
    kinds = {event.type for event in events}
    assert "result" in kinds
    assert "partial" in kinds
    result_event = next(event for event in events if event.type == "result")
    assert result_event.get("inserted") is True
    assert result_event.get("rtf") >= 0


def test_too_short_audio_is_ignored() -> None:
    sr = 16000
    audio = np.zeros(int(sr * 0.05), np.float32)
    pipeline, bus, history, engine, _ = make_pipeline()
    assert pipeline.run(audio, sr) is None
    assert engine.calls == 0
    assert history.items() == []


def test_empty_transcription_is_not_inserted(speech_audio) -> None:
    audio, sr = speech_audio
    pipeline, bus, _, _, injector = make_pipeline(text="")
    assert pipeline.run(audio, sr) is None
    assert not injector.inserted


def test_asr_failure_emits_error(speech_audio) -> None:
    import tempfile
    from pathlib import Path

    audio, sr = speech_audio
    cfg = default_config()
    bus = EventBus()
    pipeline = DictationPipeline(
        cfg, bus, HistoryStore(path=Path(tempfile.mkdtemp()) / "h.jsonl", limit=5),
        PipelineHooks(engine_provider=lambda: _FailingEngine(), injector_factory=lambda m: FakeInjector()),
    )
    assert pipeline.run(audio, sr) is None
    events = bus.drain()
    assert any(event.type == "error" for event in events)
    assert pipeline.state == SessionState.IDLE


class _FailingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__(fail=True)


def test_pipeline_without_injector_still_reports(speech_audio) -> None:
    audio, sr = speech_audio
    cfg = default_config()
    bus = EventBus()
    pipeline = DictationPipeline(
        cfg, bus, HistoryStore(limit=5),
        PipelineHooks(engine_provider=lambda: FakeEngine(), insert=False),
    )
    result = pipeline.run(audio, sr)
    assert result is not None
    assert result.inserted is False


def test_engine_loaded_lazily(speech_audio) -> None:
    audio, sr = speech_audio
    pipeline, _, _, engine, _ = make_pipeline()
    assert engine.is_loaded() is False
    pipeline.run(audio, sr)
    assert engine.is_loaded() is True


def test_history_limits_and_ordering(tmp_path) -> None:
    store = HistoryStore(path=tmp_path / "h.jsonl", limit=10)
    for index in range(5):
        store.add_text(f"текст {index}", engine="fake", duration_s=1.0, rtf=0.1)
    items = store.items()
    assert len(items) == 5
    assert items[0].text == "текст 4"  # новые сверху
    store.clear()
    assert store.items() == []
