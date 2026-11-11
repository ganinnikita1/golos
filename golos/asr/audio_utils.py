"""Аудио-утилиты для ASR-движков.

Без тяжёлых зависимостей на уровне модуля: torch/torchaudio подтягиваются
лениво внутри ``resample()``, и только когда частота действительно отличается.
"""

from __future__ import annotations

import io
import re
import wave

import numpy as np

#: Частота, с которой работают все поддерживаемые движки.
DEFAULT_SR = 16000

#: Перекрытие соседних чанков при нарезке длинного аудио (сек).
DEFAULT_OVERLAP_S = 0.5

#: Минимальная длина «хвоста»; короче — приклеиваем к предыдущему чанку.
_MIN_TAIL_S = 1.0

#: Максимум слов, на которые ищем совпадение при склейке чанков.
_MAX_MERGE_WORDS = 16

_WORD_RE = re.compile(r"[\w\-']+", re.UNICODE)
_LEADING_JUNK = " \t,.;:!?…—-«»\"'"


def to_mono_float32(audio: object) -> np.ndarray:
    """Приводит вход к float32 mono ``[-1, 1]``, не портя данные.

    Принимает numpy-массив (int16 / float32 / float64, mono или (n, channels)),
    список чисел. Пустой/None превращается в пустой массив.
    """
    if audio is None:
        return np.zeros(0, dtype=np.float32)
    arr = np.asarray(audio)
    if arr.size == 0:
        return np.zeros(0, dtype=np.float32)

    # int16 часто приходит из wave-модуля.
    if arr.dtype == np.int16:
        arr = arr.astype(np.float32) / 32768.0
    elif arr.dtype == np.int32:
        arr = arr.astype(np.float32) / 2147483648.0
    elif arr.dtype == np.uint8:
        arr = (arr.astype(np.float32) - 128.0) / 128.0
    else:
        arr = arr.astype(np.float32, copy=False)

    if arr.ndim > 1:
        # (channels, n) или (n, channels) — усредняем по канальному измерению.
        axis = 0 if arr.shape[0] < arr.shape[1] else 1
        arr = arr.mean(axis=axis, dtype=np.float32)
    arr = np.ascontiguousarray(arr.reshape(-1), dtype=np.float32)
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def resample(audio: object, src_sr: int, dst_sr: int = DEFAULT_SR) -> np.ndarray:
    """Ресемплинг в float32 mono. Быстрый путь — torchaudio, запасной — numpy."""
    arr = to_mono_float32(audio)
    src_sr = int(src_sr or DEFAULT_SR)
    dst_sr = int(dst_sr or DEFAULT_SR)
    if arr.size == 0 or src_sr == dst_sr or src_sr <= 0 or dst_sr <= 0:
        return arr
    try:  # torch/torchaudio уже загружены к моменту вызова transcribe()
        import torch
        import torchaudio

        out = torchaudio.functional.resample(
            torch.from_numpy(arr), int(src_sr), int(dst_sr)
        )
        return np.ascontiguousarray(out.cpu().numpy(), dtype=np.float32)
    except Exception:  # pragma: no cover - страховка, torchaudio есть в venv
        n_out = int(round(arr.size * dst_sr / float(src_sr)))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        x_old = np.arange(arr.size, dtype=np.float64)
        x_new = np.linspace(0.0, arr.size - 1.0, n_out, dtype=np.float64)
        return np.interp(x_new, x_old, arr).astype(np.float32)


def chunk_audio(
    audio: object,
    sample_rate: int = DEFAULT_SR,
    max_chunk_s: float = 20.0,
    overlap_s: float = DEFAULT_OVERLAP_S,
) -> list[np.ndarray]:
    """Режет длинное аудио на перекрывающиеся чанки (каждый примерно <= max_chunk_s).

    Короткое аудио возвращается одним чанком. Слишком короткий хвост
    приклеивается к предыдущему чанку, чтобы модель не пыталась распознать
    обрывок длиной в пару сотен миллисекунд.
    """
    arr = to_mono_float32(audio)
    n = int(arr.size)
    if n == 0:
        return []
    max_len = max(1, int(round(max(0.5, float(max_chunk_s)) * sample_rate)))
    if n <= max_len:
        return [arr]

    max_overlap = max(0.0, min(float(overlap_s), float(max_chunk_s) / 4.0, 5.0))
    overlap = int(round(max_overlap * sample_rate))
    hop = max(1, max_len - overlap)
    min_tail = max(int(round(_MIN_TAIL_S * sample_rate)), 1)

    bounds: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(n, start + max_len)
        bounds.append((start, end))
        if end >= n:
            break
        start = end - overlap

    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < min_tail:
        bounds[-2] = (bounds[-2][0], n)
        bounds.pop()

    return [arr[s:e] for s, e in bounds]


def _norm_words(text: str) -> list[str]:
    return [w.casefold().replace("ё", "е") for w in _WORD_RE.findall(text or "")]


def _drop_leading_words(text: str, count: int) -> str:
    """Убирает первые ``count`` слов из строки, сохраняя остальную пунктуацию."""
    if count <= 0:
        return text
    matches = list(_WORD_RE.finditer(text))
    if len(matches) <= count:
        return ""
    cut = matches[count].start()
    return text[cut:].lstrip(_LEADING_JUNK)


def _join_texts(left: str, right: str) -> str:
    """Склеивает два куска текста с одним пробелом, если он нужен."""
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    if right[0] in ",.;:!?…)]»":
        return left + right
    return left + " " + right


def merge_texts(texts: list[str], max_overlap_words: int = _MAX_MERGE_WORDS) -> str:
    """Склеивает расшифровки перекрывающихся чанков без дублей на стыках.

    Ищем самый длинный суффикс уже собранного текста, совпадающий с префиксом
    следующего куска (по нормализованным словам), и отрезаем его.
    """
    result = ""
    result_words: list[str] = []
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        words = _norm_words(text)
        keep = 0
        if result_words and words:
            limit = min(len(result_words), len(words), int(max_overlap_words))
            for cand in range(limit, 0, -1):
                if result_words[-cand:] == words[:cand]:
                    keep = cand
                    break
        if keep:
            text = _drop_leading_words(text, keep)
        if not text:
            continue
        result = _join_texts(result, text)
        result_words = _norm_words(result)
    return result.strip()


def encode_wav(audio: object, sample_rate: int = DEFAULT_SR) -> bytes:
    """Кодирует float32 mono PCM в байты WAV (16 бит) для облачных API."""
    arr = to_mono_float32(audio)
    pcm = np.clip(arr, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()
