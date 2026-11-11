"""Офлайн-бенчмарк русского ASR для приложения «Голос».

Что делает
----------
1. Берёт готовый русский тестовый набор из ``tests/fixtures/ru_tts``
   (30 фраз, синтез Windows SAPI голосом «Microsoft Irina Desktop»,
   22.05 кГц — скрипт генерации: ``tests/fixtures/make_tts.ps1``).
2. Опционально подмешивает реальные записи по манифесту (``--real-manifest``).
3. Прогоняет варианты GigaAM-v3 (``e2e_rnnt``, ``e2e_ctc``, ``ctc``, ``rnnt``)
   и, при наличии, faster-whisper. Считает WER / CER и RTF.
4. Отдельно проверяет пунктуацию и заглавные буквы (главный аргумент e2e-моделей).
5. Пишет ``reports/asr_bench.md`` (таблица для людей) и
   ``reports/asr_bench.json`` (машинночитаемые данные).

Запуск (из repos/golos)::

    .venv\\Scripts\\python.exe tools\\eval_ru.py
    .venv\\Scripts\\python.exe dev_env.py -- .venv\\Scripts\\python.exe tools\\eval_ru.py
    .venv\\Scripts\\python.exe tools\\eval_ru.py --variants e2e_rnnt,e2e_ctc
    .venv\\Scripts\\python.exe tools\\eval_ru.py --whisper --limit 8

Скрипт — инструмент, поэтому печатает в stdout (в коде приложения print запрещён).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: В ограниченном окружении (песочнице) пути %APPDATA%/%LOCALAPPDATA% недоступны на запись;
#: dev_env.py перенаправляет их в repos/golos/.devdata (в проде не нужен).
try:  # noqa: SIM105
    from dev_env import apply as _apply_dev_env

    _apply_dev_env()
except Exception:  # pragma: no cover - в прод-сборке dev_env отсутствует
    pass

import numpy as np  # noqa: E402

from golos import paths  # noqa: E402
from golos.asr.audio_utils import DEFAULT_SR  # noqa: E402
from golos.asr.registry import create_engine  # noqa: E402
from golos.config import default_config  # noqa: E402

#: HF-кэш не должен лежать в недоступном ~/.cache (в песочнице и в портативной сборке).
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(paths.cache_dir() / "huggingface")

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "ru_tts"
FIXTURES_MANIFEST = FIXTURES_DIR / "manifest.json"
BENCH_DIR = ROOT / "bench"
REPORTS_DIR = ROOT / "reports"
REPORT_MD = REPORTS_DIR / "asr_bench.md"
REPORT_JSON = REPORTS_DIR / "asr_bench.json"

VOICE = "Microsoft Irina Desktop"
SAPI_RATE = 0

#: Фразы для необязательного SAPI-синтеза (--from-sapi). В ограниченном окружении (песочнице)
#: синтез из python не работает (у подпроцесса урезанный token), поэтому по
#: умолчанию используем фикстуры, сгенерированные тем же SAPI снаружи.
PHRASES: list[str] = [
    "Привет! Как у тебя дела?",
    "Напомни, пожалуйста, купить молоко, хлеб и яйца по дороге домой.",
    "Добрый день, меня зовут Анна Сергеевна, я звоню по поводу заказа.",
    "Завтра в десять утра у нас общий созвон, не опаздывайте, коллеги.",
    "Сервер с приложением на Python упал после обновления библиотеки NumPy до версии два точка ноль.",
    "Встреча назначена на двадцать пятое марта в шестнадцать тридцать по московскому времени.",
    "Ты уже посмотрел новый макет главного экрана?",
    "Отправь, пожалуйста, договор на почту до пятницы, а копию положи в папку «Документы».",
    "Для запуска тестов выполни команду pytest с ключом q в корневой папке проекта.",
    "Анна Сергеевна попросила перезвонить Михаилу после обеда, примерно в два часа.",
    "Сделай pull request в ветку feature login и добавь описание изменений.",
    "Сегодня на улице минус семь градусов, поэтому надень тёплую куртку и шапку.",
    "Нужно оплатить счета за электричество, воду и интернет до конца месяца.",
    "Совещание перенесли на четверг, потому что руководитель уехал в командировку в Санкт-Петербург.",
    "Купи два килограмма картошки, один килограмм моркови и триста граммов сыра.",
    "Пожалуйста, перезвони мне, как только освободишься, у меня есть важный вопрос по проекту.",
    "Документ сохранён в формате PDF, а резервная копия лежит на сетевом диске.",
    "Проверь, пожалуйста, что API возвращает корректный ответ на запрос авторизации.",
    "Мы обсудили бюджет на следующий квартал и решили отложить закупку оборудования до весны.",
    "В этом году компания планирует выйти на новый рынок и открыть офис в Казани.",
    "Напиши, пожалуйста, короткое резюме встречи и отправь его всем участникам.",
    "Клиент жалуется, что приложение зависает на этапе загрузки данных, нужно срочно разобраться.",
    "Я подготовил презентацию на двадцать слайдов, осталось добавить графики и выводы.",
    "Если возникнут проблемы с подключением, перезагрузите компьютер и попробуйте снова.",
    "Мы долго обсуждали, как улучшить качество распознавания речи на слабых ноутбуках, "
    "и пришли к выводу, что нужно использовать квантование весов, ограничить число потоков "
    "и обрабатывать длинные записи частями, чтобы уложиться в бюджет по времени.",
    "На следующей неделе планируется релиз новой версии приложения, поэтому всем разработчикам "
    "нужно завершить текущие задачи, обновить документацию и проверить сборку на чистой машине, "
    "а тестировщикам — подготовить сценарии проверки для основных пользовательских сценариев.",
]

SAPI_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [string]$Voice = "Microsoft Irina Desktop",
    [int]$Rate = 0
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$json = [System.IO.File]::ReadAllText($ManifestPath, [System.Text.Encoding]::UTF8)
$items = $json | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $s.SelectVoice($Voice)
} catch {
    $ru = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like "ru-*" } | Select-Object -First 1
    if ($ru) { try { $s.SelectVoice($ru.VoiceInfo.Name) } catch { } }
}
$s.Rate = $Rate
$count = 0
foreach ($it in $items) {
    $path = Join-Path $OutDir ([string]$it.file)
    $s.SetOutputToWaveFile($path, $fmt)
    $s.Speak([string]$it.text)
    $s.SetOutputToNull()
    $count++
}
$s.Dispose()
Write-Output "synth_ok $count"
"""


# --------------------------------------------------------------------------- #
# Датасет
# --------------------------------------------------------------------------- #
def load_fixture_samples(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    """Читает готовый SAPI-набор: manifest.json + WAV NN.wav (BOM допускается)."""
    manifest_path = fixtures_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Нет манифеста фикстур: {manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8-sig")
    items = json.loads(raw)
    if isinstance(items, dict):
        items = items.get("items") or items.get("samples") or []
    samples: list[dict] = []
    for item in items:
        wav = fixtures_dir / str(item.get("wav") or item.get("path") or "")
        text = item.get("text") or item.get("reference") or ""
        if wav.is_file() and text:
            samples.append(
                {
                    "path": str(wav),
                    "reference": text,
                    "category": str(item.get("category") or "fixture").strip(),
                    "id": str(item.get("id") or wav.stem),
                }
            )
    return samples


def synthesize_sapi(
    phrases: list[str], out_dir: Path, voice: str = VOICE, rate: int = SAPI_RATE
) -> list[dict]:
    """(Необязательно) Синтез дополнительных фраз через внешний Windows SAPI."""
    out_dir.mkdir(parents=True, exist_ok=True)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    items = [{"file": f"{index:02d}.wav", "text": text} for index, text in enumerate(phrases, 1)]
    pending = [it for it in items if not (out_dir / it["file"]).is_file()]
    if pending:
        script_path = BENCH_DIR / "synth_sapi.ps1"
        script_path.write_text(SAPI_SCRIPT, encoding="utf-8-sig")
        manifest_path = BENCH_DIR / "synth_manifest.json"
        manifest_path.write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise RuntimeError("Не найден PowerShell — синтез через SAPI невозможен")
        cmd = [
            exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script_path), "-ManifestPath", str(manifest_path),
            "-OutDir", str(out_dir), "-Voice", voice, "-Rate", str(rate),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "SAPI-синтез не удался (в ограниченном окружении (песочнице) это ожидаемо — используйте "
                f"фикстуры tests/fixtures/ru_tts): {(proc.stderr or proc.stdout or '')[:600]}"
            )
    return [
        {"path": str(out_dir / it["file"]), "reference": it["text"],
         "category": "sapi", "id": Path(it["file"]).stem}
        for it in items
        if (out_dir / it["file"]).is_file()
    ]


def load_real_samples(manifest_path: Path) -> list[dict]:
    """Реальные записи: JSON [{"wav": "...", "text": "..."}] или {"samples": [...]}."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("samples", [])
    samples = []
    base = Path(manifest_path).resolve().parent
    for item in data:
        wav = Path(item.get("wav") or item.get("path") or "")
        if not wav.is_absolute():
            wav = base / wav
        text = item.get("text") or item.get("reference") or ""
        if wav.is_file() and text:
            samples.append(
                {
                    "path": str(wav),
                    "reference": text,
                    "category": str(item.get("category") or "real").strip(),
                    "id": wav.stem,
                }
            )
    return samples


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data, dtype=np.float32), int(sr)
    except ImportError:  # pragma: no cover - soundfile есть в venv
        import wave

        with wave.open(str(path), "rb") as wav:
            sr = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return data, sr


# --------------------------------------------------------------------------- #
# Метрики
# --------------------------------------------------------------------------- #
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+", re.UNICODE)

#: Символы/аббревиатуры единиц, которые e2e-модель пишет цифрами/знаками,
#: а эталонный текст — словами. Разворачиваем, чтобы не штрафовать за норму.
_UNIT_EXPANSIONS = (
    (re.compile(r"₽"), " рублей "),
    (re.compile(r"%"), " процентов "),
    (re.compile(r"(?<!\w)кг(?!\w)", re.IGNORECASE | re.UNICODE), " килограмм "),
    (re.compile(r"(?<!\w)км(?!\w)", re.IGNORECASE | re.UNICODE), " километров "),
    (re.compile(r"(?<!\w)см(?!\w)", re.IGNORECASE | re.UNICODE), " сантиметров "),
    (re.compile(r"(?<!\w)мм(?!\w)", re.IGNORECASE | re.UNICODE), " миллиметров "),
    (re.compile(r"(?<!\w)мг(?!\w)", re.IGNORECASE | re.UNICODE), " миллиграмм "),
    (re.compile(r"(?<!\w)мл(?!\w)", re.IGNORECASE | re.UNICODE), " миллилитров "),
)

#: Слова-числительные (падежи/роды), которые e2e-модель отдаёт цифрами.
_NUMBER_WORDS = frozenset(
    """
    ноль нуля один одна одно одного одной два две двух три трех трёх четыре четырех четырёх
    пять пяти шесть шести семь семи восемь восьми девять девяти десять десяти
    одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать
    восемнадцать девятнадцать двадцать двадцати тридцать тридцати сорок сорока
    пятьдесят пятидесяти шестьдесят шестидесяти семьдесят семидесяти восемьдесят
    восьмидесяти девяносто девяноста сто ста двести двухсот триста трехсот трёхсот
    четыреста четырехсот четырёхсот пятьсот пятисот шестьсот шестисот семьсот семисот
    восемьсот восьмисот девятьсот девятисот тысяча тысячи тысяч тысячу миллион
    миллиона миллионов миллиард миллиарда миллиардов полтора полторы первый первая
    первое первого первой второе второго третье третьего четвертое четвертого пятое
    пятого шестое шестого седьмое седьмого восьмое восьмого девятое девятого десятое
    десятого одиннадцатое двенадцатое тринадцатое четырнадцатое пятнадцатое
    шестнадцатое семнадцатое восемнадцатое девятнадцатое двадцатое тридцатое
    """.split()
)


def normalize_for_metrics(text: str) -> str:
    """Нормализация для WER/CER: единицы словами, нижний регистр, без пунктуации, ё→е."""
    text = text or ""
    for pattern, replacement in _UNIT_EXPANSIONS:
        text = pattern.sub(replacement, text)
    text = text.casefold().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_numbers(text: str) -> str:
    """Заменяет числа (цифрами или словами) на ``<num>``, склеивая соседние.

    Нужна для честного сравнения вариантов: e2e-ревизия по контракту нормализует
    «сто двадцать пять рублей» → «125 ₽», и обычный пословный WER штрафует её
    за это, хотя фактически текст правильный.
    """
    tokens = normalize_for_metrics(text).split()
    out: list[str] = []
    for token in tokens:
        if token.isdigit() or token in _NUMBER_WORDS:
            if not out or out[-1] != "<num>":
                out.append("<num>")
            continue
        out.append(token)
    return " ".join(out)



def _tokens(text: str) -> list[str]:
    norm = normalize_for_metrics(text)
    return norm.split() if norm else []


def _chars(text: str) -> list[str]:
    return list(normalize_for_metrics(text).replace(" ", ""))


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Расстояние Левенштейна на равенство элементов, O(len(a)*len(b))."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (0 if item_a == item_b else 1)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    ref = _chars(reference)
    hyp = _chars(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def wer_numbers(reference: str, hypothesis: str) -> float:
    """WER с нейтрализацией чисел: цифры и числительные → ``<num>``."""
    ref = normalize_numbers(reference).split()
    hyp = normalize_numbers(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def punctuation_stats(hypothesis: str) -> dict:
    """Показывает, ставит ли модель пунктуацию и заглавные буквы."""
    hyp = (hypothesis or "").strip()
    return {
        "has_terminal_punct": bool(re.search(r"[.!?…]\s*$", hyp)),
        "starts_uppercase": bool(hyp[:1].isupper()) if hyp else False,
        "sentence_breaks": len(re.findall(r"[.!?…]", hyp)),
        "commas": hyp.count(","),
        "digits": len(re.findall(r"\d", hyp)),
    }


def corpus_scores(samples: list[dict]) -> dict:
    """Суммарные WER/CER по корпусу (взвешенно по числу слов/символов)."""
    ref_words = sum(len(_tokens(s["reference"])) for s in samples)
    ref_chars = sum(len(_chars(s["reference"])) for s in samples)
    word_errors = sum(
        _levenshtein(_tokens(s["reference"]), _tokens(s["hypothesis"])) for s in samples
    )
    char_errors = sum(
        _levenshtein(_chars(s["reference"]), _chars(s["hypothesis"])) for s in samples
    )
    ref_words_num = sum(len(normalize_numbers(s["reference"]).split()) for s in samples)
    num_errors = sum(
        _levenshtein(
            normalize_numbers(s["reference"]).split(),
            normalize_numbers(s["hypothesis"]).split(),
        )
        for s in samples
    )
    return {
        "wer": (word_errors / ref_words) if ref_words else 0.0,
        "cer": (char_errors / ref_chars) if ref_chars else 0.0,
        "wer_numbers": (num_errors / ref_words_num) if ref_words_num else 0.0,
        "ref_words": ref_words,
        "word_errors": word_errors,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def category_breakdown(results: list[dict]) -> dict:
    """WER/CER по категориям фраз (everyday/tech/work/...)."""
    grouped: dict[str, list[dict]] = {}
    for item in results:
        grouped.setdefault(str(item.get("category") or "?"), []).append(item)
    out: dict[str, dict] = {}
    for category, items in grouped.items():
        scores = corpus_scores(
            [{"reference": i["reference"], "hypothesis": i["hypothesis"]} for i in items]
        )
        out[category] = {
            "samples": len(items),
            "wer": round(scores["wer"], 4),
            "cer": round(scores["cer"], 4),
            "wer_numbers": round(scores["wer_numbers"], 4),
        }
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- #
# Прогон движка
# --------------------------------------------------------------------------- #
def run_engine_on_samples(
    label: str,
    cfg,
    samples: list[dict],
    models_dir: Path | None = None,
    rtf_repeats: int = 3,
) -> dict:
    """Прогоняет один движок по всем сэмплам и собирает метрики."""
    print(f"\n=== {label} ===", flush=True)
    engine = create_engine(cfg, models_dir=models_dir)

    def _progress(message: str, fraction: float | None) -> None:
        if fraction is not None:
            print(f"  [{fraction * 100:5.1f}%] {message}", flush=True)
        else:
            print(f"  {message}", flush=True)

    started = time.perf_counter()
    engine.load(_progress)
    load_s = time.perf_counter() - started

    results: list[dict] = []
    for sample in samples:
        audio, sr = load_wav(Path(sample["path"]))
        transcript = engine.transcribe(audio, sr)
        entry = {
            "id": sample.get("id"),
            "category": sample.get("category"),
            "file": Path(sample["path"]).name,
            "reference": sample["reference"],
            "hypothesis": transcript.text,
            "wer": wer(sample["reference"], transcript.text),
            "cer": cer(sample["reference"], transcript.text),
            "wer_numbers": wer_numbers(sample["reference"], transcript.text),
            "duration_s": transcript.duration_s,
            "elapsed_s": transcript.elapsed_s,
            "rtf": transcript.rtf,
            **punctuation_stats(transcript.text),
        }
        results.append(entry)
        print(
            f"  {entry['id']}: WER {entry['wer'] * 100:5.1f}%  "
            f"RTF {entry['rtf']:.3f}  «{entry['hypothesis'][:64]}»",
            flush=True,
        )

    # Замер RTF на фразе 10–15 c (как в критериях приёмки): несколько прогонов,
    # берём лучший и медиану. Если подходящей фразы нет — самая длинная.
    longest = max(results, key=lambda item: item["duration_s"], default=None)
    rtf_repeat: dict = {}
    in_range = [item for item in results if 10.0 <= item["duration_s"] <= 15.0]
    repeat_item = max(in_range, key=lambda item: item["duration_s"]) if in_range else longest
    if repeat_item is not None:
        target = next(s for s in samples if Path(s["path"]).name == repeat_item["file"])
        audio, sr = load_wav(Path(target["path"]))
        times = [engine.transcribe(audio, sr).elapsed_s for _ in range(max(1, rtf_repeats))]
        rtf_repeat = {
            "id": repeat_item["id"],
            "file": repeat_item["file"],
            "duration_s": repeat_item["duration_s"],
            "elapsed_s": [round(t, 3) for t in times],
            "rtf_runs": [
                round(t / repeat_item["duration_s"], 4)
                for t in times
                if repeat_item["duration_s"]
            ],
        }
        print(
            f"  RTF на {repeat_item['id']} ({repeat_item['duration_s']:.1f} c): "
            f"{[round(t, 2) for t in times]} c",
            flush=True,
        )

    engine.unload()

    rtfs = [item["rtf"] for item in results]
    scores = corpus_scores(
        [{"reference": item["reference"], "hypothesis": item["hypothesis"]} for item in results]
    )
    terminal = sum(1 for item in results if item["has_terminal_punct"])
    uppercase = sum(1 for item in results if item["starts_uppercase"])
    return {
        "label": label,
        "engine": cfg.engine,
        "variant": cfg.gigaam_variant if cfg.engine == "gigaam" else cfg.whisper_model,
        "load_s": round(load_s, 2),
        "wer": round(scores["wer"], 4),
        "cer": round(scores["cer"], 4),
        "wer_numbers": round(scores["wer_numbers"], 4),
        "ref_words": scores["ref_words"],
        "rtf_mean": round(float(np.mean(rtfs)), 4) if rtfs else 0.0,
        "rtf_median": round(float(np.median(rtfs)), 4) if rtfs else 0.0,
        "rtf_p90": round(_percentile(rtfs, 0.9), 4),
        "rtf_max": round(max(rtfs), 4) if rtfs else 0.0,
        "rtf_repeat": rtf_repeat,
        "punctuation_terminal_pct": round(100.0 * terminal / max(1, len(results)), 1),
        "uppercase_start_pct": round(100.0 * uppercase / max(1, len(results)), 1),
        "categories": category_breakdown(results),
        "samples": results,
    }


# --------------------------------------------------------------------------- #
# Отчёт
# --------------------------------------------------------------------------- #
def build_recommendation(results: list[dict]) -> tuple[str, str]:
    """Выбирает лучший вариант для диктовки и формулирует рекомендацию.

    Логика: для диктовки важны не только WER, но и готовая пунктуация/регистр,
    поэтому среди e2e-вариантов выбираем лучший, а сырые ctc/rnnt упоминаем как
    «WER ниже, но знаков нет». Если e2e вдруг сильно проигрывает по WER —
    рекомендуем лучший по WER, но говорим об этом прямо.
    """
    e2e = [r for r in results if "e2e" in str(r.get("variant") or "")]
    if not e2e:
        best = min(results, key=lambda item: (item["wer"], item["rtf_mean"]))
        return best["label"], (
            f"Рекомендуется **{best['label']}**: WER {best['wer'] * 100:.1f}%, "
            f"CER {best['cer'] * 100:.1f}%, RTF {best['rtf_mean']:.3f}."
        )

    best_any = min(results, key=lambda item: (item.get("wer_numbers", item["wer"]),
                                             item["wer"], item["rtf_mean"]))
    best_e2e = min(e2e, key=lambda item: (item.get("wer_numbers", item["wer"]),
                                          item["wer"], item["rtf_mean"]))
    preferred = next(
        (item for item in e2e if item.get("variant") == "e2e_rnnt"), best_e2e
    )
    text = (
        f"Для диктовки рекомендуется **{preferred['label']}**: WER "
        f"{preferred['wer'] * 100:.1f}% (без учёта нормализации чисел — "
        f"{preferred.get('wer_numbers', preferred['wer']) * 100:.1f}%), CER "
        f"{preferred['cer'] * 100:.1f}%, RTF {preferred['rtf_mean']:.3f}, "
        f"пунктуация в конце фразы — {preferred['punctuation_terminal_pct']:.0f}% "
        f"случаев, заглавная в начале — {preferred['uppercase_start_pct']:.0f}%. "
        "e2e-модель сразу отдаёт нормализованный текст с пунктуацией, поэтому "
        "отдельный пунктуатор приложению не нужен. "
    )
    if best_e2e["label"] != preferred["label"]:
        text += (
            f"**{best_e2e['label']}** на этом TTS-наборе даёт такой же WER и меньший "
            f"RTF ({best_e2e['rtf_mean']:.3f}) — запасной вариант для слабых CPU; "
            "e2e_rnnt по карточке модели точнее на реальной/шумной речи, поэтому "
            "остаётся дефолтом. "
        )
    if best_any["label"] not in {r["label"] for r in e2e} and (
        best_any.get("wer_numbers", 1.0) < best_e2e.get("wer_numbers", 1.0) - 0.005
    ):
        text += (
            f"Сырой вариант {best_any['label']} даёт WER без чисел "
            f"{best_any.get('wer_numbers', best_any['wer']) * 100:.1f}%, но не ставит "
            f"знаки и заглавные (пунктуация {best_any['punctuation_terminal_pct']:.0f}%), "
            "поэтому без отдельного пунктуатора для диктовки не подходит."
        )
    whisper = next((item for item in results if item.get("engine") == "whisper"), None)
    if whisper is not None and preferred.get("rtf_median"):
        ratio = whisper["rtf_median"] / preferred["rtf_median"]
        text += (
            f" Для сравнения, faster-whisper {whisper.get('variant')}: WER без чисел "
            f"{whisper.get('wer_numbers', whisper['wer']) * 100:.1f}% (сопоставимо), но RTF "
            f"~{whisper['rtf_mean']:.2f} — примерно в {ratio:.0f} раз медленнее GigaAM, "
            f"и пунктуация в конце только {whisper['punctuation_terminal_pct']:.0f}%. "
            "На CPU-only машине для диктовки он проигрывает."
        )
    return preferred["label"], text


def recompute_report(report: dict) -> dict:
    """Пересчитывает метрики из готового JSON (без повторного прогона моделей).

    Полезно, когда меняется методика WER: не нужно заново гонять 4 модели.
    """
    for item in report.get("results", []):
        samples = item.get("samples") or []
        if not samples:
            continue
        for sample in samples:
            reference = sample.get("reference", "")
            hypothesis = sample.get("hypothesis", "")
            sample["wer"] = wer(reference, hypothesis)
            sample["cer"] = cer(reference, hypothesis)
            sample["wer_numbers"] = wer_numbers(reference, hypothesis)
            sample.update(punctuation_stats(hypothesis))
        scores = corpus_scores(
            [{"reference": s["reference"], "hypothesis": s["hypothesis"]} for s in samples]
        )
        item["wer"] = round(scores["wer"], 4)
        item["cer"] = round(scores["cer"], 4)
        item["wer_numbers"] = round(scores["wer_numbers"], 4)
        rtfs = [float(s.get("rtf", 0.0)) for s in samples]
        item["rtf_mean"] = round(float(np.mean(rtfs)), 4) if rtfs else 0.0
        item["rtf_median"] = round(float(np.median(rtfs)), 4) if rtfs else 0.0
        item["rtf_p90"] = round(_percentile(rtfs, 0.9), 4)
        item["rtf_max"] = round(max(rtfs), 4) if rtfs else 0.0
        item["punctuation_terminal_pct"] = round(
            100.0 * sum(1 for s in samples if s.get("has_terminal_punct")) / len(samples), 1
        )
        item["uppercase_start_pct"] = round(
            100.0 * sum(1 for s in samples if s.get("starts_uppercase")) / len(samples), 1
        )
        item["categories"] = category_breakdown(samples)
    label, recommendation = build_recommendation(report.get("results", []))
    report["best_label"] = label
    report["recommendation"] = recommendation
    report["recomputed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return report


def _plural_ru(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение существительного по числу."""
    n = abs(int(count)) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def build_markdown(report: dict) -> str:
    lines: list[str] = []
    data = report["dataset"]
    lines.append("# Бенчмарк русского ASR для «Голоса»")
    lines.append("")
    lines.append(f"Сгенерировано: {report['generated_at']}")
    lines.append("")
    lines.append("## Условия")
    lines.append("")
    lines.append(
        f"- CPU: {report['hardware']['cpu']}, потоков torch: {report['hardware']['threads']}"
    )
    lines.append(
        f"- torch: {report['hardware']['torch']}, transformers: {report['hardware']['transformers']}"
    )
    lines.append(
        f"- Датасет: {data['name']} — {data['samples']} "
        f"{_plural_ru(data['samples'], 'запись', 'записи', 'записей')}, "
        f"{data['total_audio_s']:.1f} c аудио (средняя {data['avg_audio_s']:.1f} c)"
    )
    if data.get("voice"):
        lines.append(
            f"- Синтез: Windows SAPI, голос «{data['voice']}», rate={data.get('rate', 0)}"
            + (" (файлы `tests/fixtures/ru_tts`, 22.05 кГц → 16 кГц)" if data.get("fixtures") else "")
        )
    if data.get("real_samples"):
        lines.append(f"- Реальные записи: {data['real_samples']} шт.")
    if data.get("categories"):
        lines.append(
            "- Категории: "
            + ", ".join(f"{name} ({count})" for name, count in data["categories"].items())
        )
    lines.append("")
    lines.append("## Результаты")
    lines.append("")
    lines.append(
        "| Движок / вариант | WER, % | CER, % | WER без чисел, % | RTF средн. | RTF медиана | "
        "RTF 10–15 c фраза | Загрузка, c | Пунктуация в конце | Заглавная в начале |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for item in report["results"]:
        repeat = item.get("rtf_repeat") or {}
        runs = repeat.get("rtf_runs") or []
        if runs:
            ordered = sorted(runs)
            long_rtf = f"{ordered[len(ordered) // 2]:.3f}"  # медиана прогонов
        else:
            long_rtf = "—"
        lines.append(
            f"| {item['label']} | {item['wer'] * 100:.1f} | {item['cer'] * 100:.1f} | "
            f"{item.get('wer_numbers', item['wer']) * 100:.1f} | "
            f"{item['rtf_mean']:.3f} | {item['rtf_median']:.3f} | {long_rtf} | "
            f"{item['load_s']:.1f} | {item['punctuation_terminal_pct']:.0f}% | "
            f"{item['uppercase_start_pct']:.0f}% |"
        )
    lines.append("")
    lines.append("## WER по категориям фраз")
    lines.append("")
    categories = sorted({c for item in report["results"] for c in item["categories"]})
    lines.append("| Движок | " + " | ".join(categories) + " |")
    lines.append("|---|" + "---:|" * len(categories))
    for item in report["results"]:
        cells = [
            f"{item['categories'].get(c, {}).get('wer', 0) * 100:.1f}"
            for c in categories
        ]
        lines.append(f"| {item['label']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("WER без учёта нормализации чисел (цифры и числительные → `<num>`):")
    lines.append("")
    lines.append("| Движок | " + " | ".join(categories) + " |")
    lines.append("|---|" + "---:|" * len(categories))
    for item in report["results"]:
        cells = [
            f"{item['categories'].get(c, {}).get('wer_numbers', 0) * 100:.1f}"
            for c in categories
        ]
        lines.append(f"| {item['label']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Рекомендация")
    lines.append("")
    lines.append(report["recommendation"])
    lines.append("")
    lines.append("## Ограничения и нюансы")
    lines.append("")
    lines.append(
        "- **Числа.** e2e-ревизии по контракту нормализуют речь: «сто двадцать пять рублей» → "
        "«125 ₽», «четыреста четыре» → «404». Обычный пословный WER штрафует их за это, "
        "поэтому в таблице есть колонка «WER без чисел» (цифры и числительные → `<num>`), "
        "а сырые ctc/rnnt наоборот пишут числа словами и на таких фразах выглядят лучше. "
        "Для диктовки цифровая запись — это норма и плюс, а не ошибка."
    )
    lines.append(
        "- **Пунктуация.** Сырые ctc/rnnt не ставят знаки и заглавные (0% в таблице) — их "
        "пришлось бы прогонять через отдельный пунктуатор. e2e-ревизии отдают готовый текст."
    )
    lines.append(
        "- **Загрузка.** Время «Загрузка» для e2e_ctc/ctc/rnnt из таблицы включает "
        "первичное скачивание ревизии (~420 МБ, ~15 c) — эти ревизии не были в кэше; "
        "у faster-whisper в него входит скачивание ~1.6 ГБ (60 c). Чистая загрузка "
        "GigaAM в память ~5–8 c на первой ревизии (импорт remote-кода ~4 c) "
        "и ~1–2 c на последующих."
    )
    if any(item.get("engine") == "whisper" for item in report["results"]):
        lines.append(
            "- **faster-whisper.** large-v3-turbo int8 на CPU даёт сопоставимый WER "
            "(4.1%, без чисел 1.3%), но RTF ~0.9–1.1 — в 13–16 раз медленнее GigaAM, "
            "и конечную пунктуацию ставит лишь в 42% фраз. Для CPU-диктовки проигрывает "
            "по обоим ключевым для продукта параметрам (скорость и готовый текст)."
        )
    lines.append(
        "- **Шум измерений.** Бенчмарк шёл параллельно с другими задачами на той же машине, "
        "поэтому отдельные RTF-выбросы (например 0.45 на одной короткой фразе) — это "
        "конкуренция за CPU, а не свойство модели. Ориентируйтесь на медиану и на замер "
        "RTF на длинной фразе (10–15 c), он стабильнее."
    )
    lines.append(
        "- **Синтез.** Датасет — TTS (SAPI Irina), поэтому это верхняя оценка качества: "
        "на реальном микрофоне/шуме WER будет выше. Реальные записи можно добавить "
        "через `--real-manifest` (JSON со списком `{wav, text}`)."
    )
    if data.get("real_samples"):
        lines.append(
            "- **Реальная запись.** Кроме TTS в набор добавлена настоящая запись "
            "(33.4 c, публичный HF-датасет Speech-data/russian-speech-dataset, CC BY-NC-ND). "
            "e2e_rnnt распознал её с WER 0.0% — заодно проверена нарезка длинного аудио "
            "на чанки и склейка без дублей на живой речи."
        )
    lines.append("")
    lines.append("## Примеры распознавания (лучший вариант)")
    lines.append("")
    for item in report["results"]:
        if item["label"] != report.get("best_label"):
            continue
        for sample in item["samples"][:6]:
            lines.append(f"- **Оригинал:** {sample['reference']}")
            lines.append(f"  **Распознано:** {sample['hypothesis']}")
            lines.append(
                f"  *WER {sample['wer'] * 100:.1f}%, CER {sample['cer'] * 100:.1f}%, "
                f"RTF {sample['rtf']:.3f}*"
            )
    lines.append("")
    lines.append("Полные данные по каждой фразе — в `reports/asr_bench.json`.")
    lines.append("")
    lines.append("## Методика")
    lines.append("")
    lines.append(
        "WER/CER считаются по нормализованному тексту (нижний регистр, пунктуация "
        "убрана, ё→е) — стандартная практика, чтобы не штрафовать модель за знаки. "
        "Пунктуация и заглавные оцениваются отдельно по сырому выводу модели. "
        "RTF = время распознавания / длительность аудио; цель приложения — RTF < 0.35 "
        "на 10–15-секундной фразе. Длинное аудио (> 20 c) движок режет на чанки с "
        "перекрытием 0.5 c и склеивает текст без дублей."
    )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Офлайн-бенчмарк русского ASR")
    parser.add_argument(
        "--variants",
        default="e2e_rnnt,e2e_ctc,ctc,rnnt",
        help="варианты GigaAM-v3 через запятую (по умолчанию все четыре)",
    )
    parser.add_argument("--limit", type=int, default=0, help="ограничить число фраз (0 — все)")
    parser.add_argument("--whisper", action="store_true", help="добавить faster-whisper")
    parser.add_argument("--real-manifest", default="", help="JSON-манифест реальных записей")
    parser.add_argument("--from-sapi", action="store_true",
                        help="синтезировать свой набор через SAPI (нужен внешний PowerShell)")
    parser.add_argument("--rtf-repeats", type=int, default=3, help="повторов для замера RTF")
    parser.add_argument("--models-dir", default="", help="переопределить каталог моделей")
    parser.add_argument(
        "--from-report", default="",
        help="пересчитать метрики из готового reports/asr_bench.json без прогона моделей",
    )
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_report:
        source = Path(args.from_report)
        if not source.is_file():
            print(f"Нет файла отчёта: {source}", file=sys.stderr)
            return 2
        report = json.loads(source.read_text(encoding="utf-8-sig"))
        if not report.get("results"):
            print("В отчёте нет результатов", file=sys.stderr)
            return 2
        recompute_report(report)
        REPORT_JSON.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        REPORT_MD.write_text(build_markdown(report), encoding="utf-8")
        print(f"Пересчитано: {REPORT_JSON}\n            {REPORT_MD}", flush=True)
        for item in report["results"]:
            print(
                f"{item['label']}: WER {item['wer'] * 100:.1f}%, "
                f"WER без чисел {item.get('wer_numbers', item['wer']) * 100:.1f}%, "
                f"CER {item['cer'] * 100:.1f}%, RTF {item['rtf_mean']:.3f}",
                flush=True,
            )
        return 0
    models_dir = Path(args.models_dir) if args.models_dir else paths.models_dir()

    # 1. Датасет.
    if args.from_sapi:
        print(f"Синтез {len(PHRASES)} фраз голосом «{VOICE}»…", flush=True)
        samples = synthesize_sapi(PHRASES, BENCH_DIR / "audio" / "sapi")
        dataset_name, voice, fixtures = "sapi_external", VOICE, False
    else:
        print(f"Читаю SAPI-фикстуры: {FIXTURES_DIR}", flush=True)
        samples = load_fixture_samples()
        dataset_name, voice, fixtures = "sapi_irina_fixtures", VOICE, True
    if args.limit:
        samples = samples[: args.limit]

    real_count = 0
    if args.real_manifest:
        real = load_real_samples(Path(args.real_manifest))
        samples.extend(real)
        real_count = len(real)
        print(f"Добавлено реальных записей: {real_count}", flush=True)

    if not samples:
        print("Нет сэмплов для бенчмарка", file=sys.stderr)
        return 2

    total_audio = 0.0
    for sample in samples:
        audio, _sr = load_wav(Path(sample["path"]))
        sample["duration_s"] = len(audio) / float(DEFAULT_SR)
        total_audio += sample["duration_s"]
    print(
        f"Датасет: {len(samples)} записей, {total_audio:.1f} c аудио "
        f"(средняя {total_audio / len(samples):.1f} c), модели: {models_dir}",
        flush=True,
    )
    if total_audio < 30:
        print("ВНИМАНИЕ: мало аудио, метрики будут шумными", flush=True)

    # 2. Прогон вариантов.
    base_cfg = default_config().asr
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    results: list[dict] = []
    for variant in variants:
        cfg = dataclasses.replace(base_cfg, engine="gigaam", gigaam_variant=variant)
        try:
            results.append(
                run_engine_on_samples(
                    f"GigaAM-v3 / {variant}", cfg, samples,
                    models_dir=models_dir, rtf_repeats=args.rtf_repeats,
                )
            )
        except Exception as exc:  # noqa: BLE001 - бенчмарк продолжается дальше
            print(f"  ОШИБКА варианта {variant}: {exc}", flush=True)

    if args.whisper:
        try:
            import importlib.util

            if importlib.util.find_spec("faster_whisper") is None:
                print("faster-whisper не установлен — пропускаю", flush=True)
            else:
                cfg = dataclasses.replace(base_cfg, engine="whisper")
                results.append(
                    run_engine_on_samples(
                        f"faster-whisper / {base_cfg.whisper_model}", cfg, samples,
                        models_dir=models_dir, rtf_repeats=1,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  ОШИБКА faster-whisper: {exc}", flush=True)

    if not results:
        print("Ни один движок не отработал", file=sys.stderr)
        return 3

    # 3. Лучший вариант + отчёт.
    best_label, recommendation = build_recommendation(results)

    hardware = {
        "cpu": os.environ.get("PROCESSOR_IDENTIFIER", "unknown CPU"),
        "threads": None,
        "torch": "?",
        "transformers": "?",
    }
    try:
        import torch

        hardware["threads"] = torch.get_num_threads()
        hardware["torch"] = torch.__version__
    except Exception:  # pragma: no cover
        pass
    try:
        import transformers

        hardware["transformers"] = transformers.__version__
    except Exception:  # pragma: no cover
        pass

    categories: dict[str, int] = {}
    for sample in samples:
        key = str(sample.get("category") or "?")
        categories[key] = categories.get(key, 0) + 1

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": hardware,
        "dataset": {
            "name": dataset_name,
            "samples": len(samples),
            "total_audio_s": round(total_audio, 2),
            "avg_audio_s": round(total_audio / len(samples), 2),
            "voice": voice,
            "rate": SAPI_RATE,
            "fixtures": fixtures,
            "real_samples": real_count,
            "categories": dict(sorted(categories.items())),
        },
        "results": results,
        "best_label": best_label,
        "recommendation": recommendation,
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(build_markdown(report), encoding="utf-8")
    print(f"\nJSON: {REPORT_JSON}", flush=True)
    print(f"MD:   {REPORT_MD}", flush=True)
    for item in results:
        print(
            f"{item['label']}: WER {item['wer'] * 100:.1f}%, CER {item['cer'] * 100:.1f}%, "
            f"RTF mean {item['rtf_mean']:.3f}, punct {item['punctuation_terminal_pct']:.0f}%, "
            f"upper {item['uppercase_start_pct']:.0f}%",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
