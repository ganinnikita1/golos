"""Командная строка «Голоса» — полезно для отладки и проверки без UI.

Примеры:
    python -m golos.cli devices
    python -m golos.cli record --seconds 5 --out sample.wav
    python -m golos.cli transcribe sample.wav
    python -m golos.cli say "Привет, это проверка диктовки" --out tts.wav
    python -m golos.cli selftest
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, paths
from .audio import utils as audio_utils
from .config import load_config
from .logging_setup import get_logger, setup as setup_logging

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
def cmd_devices(_args: argparse.Namespace) -> int:
    from .audio.recorder import Recorder

    cfg = load_config()
    recorder = Recorder(cfg.audio)
    try:
        devices = recorder.devices()
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1
    if not devices:
        print("Микрофоны не найдены")
        return 1
    for item in devices:
        mark = " (по умолчанию)" if item["is_default"] else ""
        print(f"[{item['index']}] {item['name']}{mark}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .audio.recorder import Recorder

    cfg = load_config()
    recorder = Recorder(cfg.audio)
    print(f"Запись {args.seconds} c... говорите.")
    recorder.start()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("\nОстановлено")
    audio = recorder.stop()
    if audio.size == 0:
        print("Ничего не записано")
        return 1
    out = Path(args.out)
    audio_utils.write_wav(out, audio, cfg.audio.sample_rate)
    print(f"Сохранено: {out} ({audio.shape[0] / cfg.audio.sample_rate:.2f} c)")
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    from .pipeline import DictationPipeline, PipelineHooks, SessionState
    from .events import EventBus
    from .history import HistoryStore
    from .asr.registry import create_engine

    cfg = load_config()
    audio, rate = audio_utils.read_wav(args.path)
    print(f"Аудио: {audio.shape[0] / rate:.2f} c, {rate} Гц, файл {args.path}")
    bus = EventBus()
    bus.subscribe(lambda event: print(f"  [{event.type}] {event.data}") if args.verbose else None)
    engine = create_engine(cfg.asr, models_dir=paths.models_dir())

    def engine_provider():
        return engine

    pipeline = DictationPipeline(
        cfg, bus, HistoryStore(limit=5),
        PipelineHooks(engine_provider=engine_provider, insert=False),
    )
    started = time.monotonic()
    result = pipeline.run(audio, rate)
    total = time.monotonic() - started
    if result is None:
        print("Распознать не удалось")
        return 1
    print("\n--- Текст ---")
    print(result.text)
    print("-------------")
    print(
        json.dumps(
            {
                "engine": result.engine,
                "audio_s": round(result.duration_s, 2),
                "asr_s": round(result.asr_elapsed_s, 2),
                "total_s": round(total, 2),
                "rtf": round(result.rtf, 3),
                "chars": len(result.text),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Последнее состояние: {pipeline.state}")
    return 0


def cmd_say(args: argparse.Namespace) -> int:
    """Синтез русской речи через SAPI (голос Irina) — для тестов.

    System.Speech.Synthesis в этом окружении отказывается выбирать голос
    («none available with the current security setting»), поэтому используем
    COM-интерфейс SAPI.SpVoice — он работает.
    """
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass
    rate = int(args.rate)
    work = paths.cache_dir() / "tts"
    work.mkdir(parents=True, exist_ok=True)
    text_file = work / "text.txt"
    text_file.write_text(args.text, encoding="utf-8")
    script_file = work / "say.ps1"
    # Скрипт намеренно в ASCII: кириллицу читаем из отдельного UTF-8 файла,
    # иначе PowerShell 5.1 ломает кодировку при передаче через -Command.
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$v = New-Object -ComObject SAPI.SpVoice",
            "$voices = @($v.GetVoices())",
            "$ru = $voices | Where-Object { $_.GetDescription() -like '*Irina*' } | Select-Object -First 1",
            "if (-not $ru) { $ru = $voices | Where-Object { $_.GetDescription() -like '*Russian*' } | Select-Object -First 1 }",
            "if ($ru) { $v.Voice = $ru }",
            f"$v.Rate = {rate}",
            "$fs = New-Object -ComObject SAPI.SpFileStream",
            "$fs.Open('" + str(out) + "', 3, $false)",
            "$v.AudioOutputStream = $fs",
            "$text = [IO.File]::ReadAllText('" + str(text_file) + "', [Text.Encoding]::UTF8)",
            "$v.Speak($text)",
            "$fs.Close()",
            "",
        ]
    )
    script_file.write_text(script, encoding="ascii", errors="replace")
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script_file)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    if completed.returncode != 0 or not out.exists():
        print("Ошибка синтеза:", (completed.stderr or completed.stdout or "").strip()[:800])
        return 1
    audio, sr = audio_utils.read_wav(out)
    print(f"Синтезировано: {out} ({audio.shape[0] / sr:.2f} c, {sr} Гц)")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Проверка окружения: конфиг, пути, аудио, движок, вставка, хоткей, пайплайн."""
    from .asr.registry import create_engine
    from .config import default_config
    from .inject import create_injector
    from .hotkey import parse_hotkey

    results: list[tuple[str, str, str]] = []  # (name, status: ok|fail|skip, detail)
    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ru_tts"

    def check(name: str, fn, *, fatal: bool = True):
        started = time.monotonic()
        try:
            detail = fn()
            results.append((name, "ok", str(detail or "")[:200]))
        except Exception as exc:
            status = "fail" if fatal else "skip"
            results.append((name, status, f"{type(exc).__name__}: {exc}"[:300]))
        elapsed = time.monotonic() - started
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}[results[-1][1]]
        print(f"  {mark} {name} ({elapsed:.2f} c) {results[-1][2]}")

    print("Проверка окружения «Голос»")
    cfg = default_config()

    def check_config():
        from .config import load_config

        loaded = load_config()
        return f"движок={loaded.asr.engine}/{loaded.asr.gigaam_variant}, хоткей={loaded.hotkey.hotkey}"

    check("конфигурация", check_config)
    check("каталоги", lambda: paths.display_path(paths.ensure_dirs() or paths.appdata_dir()))

    def check_audio():
        from .audio.recorder import Recorder

        devices = Recorder(cfg.audio).devices()
        if not devices:
            raise RuntimeError("микрофоны не найдены")
        return f"{len(devices)} устройств: " + ", ".join(d["name"] for d in devices[:3])

    check("микрофоны", check_audio)

    # Тестовое аудио: сначала готовые фикстуры, иначе синтез SAPI.
    audio_file: Path | None = None
    if (fixtures / "01.wav").exists():
        audio_file = fixtures / "01.wav"
    else:
        def check_tts():
            out = paths.cache_dir() / "selftest_say.wav"
            code = cmd_say(argparse.Namespace(text="Привет! Это тест диктовки на русском языке.",
                                             out=str(out), rate=0))
            audio, sr = audio_utils.read_wav(out)
            assert audio.size > sr * 0.3, "слишком короткое аудио"
            return f"{audio.shape[0] / sr:.2f} c"

        # Синтез SAPI не является частью продукта — неудача не критична.
        check("синтез русской речи (SAPI)", check_tts, fatal=False)
        candidate = paths.cache_dir() / "selftest_say.wav"
        if candidate.exists():
            audio_file = candidate

    def check_engine():
        started = time.monotonic()
        engine = create_engine(cfg.asr, models_dir=paths.models_dir())
        engine.load(lambda msg, frac: print(f"      {msg}"))
        load_s = time.monotonic() - started
        if audio_file is None:
            raise RuntimeError("нет тестового аудио")
        audio, sr = audio_utils.read_wav(audio_file)
        t0 = time.monotonic()
        transcript = engine.transcribe(audio, sr)
        asr_s = time.monotonic() - t0
        rtf = asr_s / (audio.shape[0] / sr)
        print(f"      текст: {transcript.text!r}")
        return f"загрузка {load_s:.1f} c, ASR {asr_s:.2f} c, RTF {rtf:.2f}"

    check("движок распознавания", check_engine)
    if not any(name == "движок распознавания" and status == "ok" for name, status, _ in results):
        print("\nИтог: движок не загрузился — проверять пайплайн нет смысла.")
        print("Подсказка: запустите `python tools\\bootstrap_env.py`, проверьте сеть и место на диске.")
        return 1

    def check_pipeline():
        from .events import EventBus
        from .history import HistoryStore
        from .pipeline import DictationPipeline, PipelineHooks

        engine = create_engine(cfg.asr, models_dir=paths.models_dir())
        if not engine.is_loaded():
            engine.load(None)
        audio, sr = audio_utils.read_wav(audio_file)
        bus = EventBus()
        pipeline = DictationPipeline(
            cfg, bus, HistoryStore(limit=5),
            PipelineHooks(engine_provider=lambda: engine, insert=False),
        )
        result = pipeline.run(audio, sr)
        assert result is not None and result.text, "пустой результат"
        return f"«{result.text[:60]}» за {result.total_elapsed_s:.2f} c"

    check("полный пайплайн (без вставки)", check_pipeline)

    def check_inject():
        injector = create_injector("clipboard")
        assert injector is not None
        return type(injector).__name__

    check("инжектор текста", check_inject)

    def check_hotkey():
        keys = parse_hotkey("ctrl+alt+space")
        return ",".join(sorted(keys))

    check("парсер хоткея", check_hotkey)

    failed = [item for item in results if item[1] == "fail"]
    skipped = [item for item in results if item[1] == "skip"]
    print(f"\nИтог: {len(results) - len(failed) - len(skipped)}/{len(results)} проверок пройдено"
          + (f", пропущено {len(skipped)}" if skipped else ""))
    if failed:
        print("Не прошли:", ", ".join(item[0] for item in failed))
        return 1
    print(f"Версия {__version__}; конфиг: {paths.display_path(paths.config_path())}")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="golos-cli", description="Отладка «Голоса»")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="список микрофонов").set_defaults(func=cmd_devices)

    record = sub.add_parser("record", help="записать фрагмент")
    record.add_argument("--seconds", type=float, default=5.0)
    record.add_argument("--out", type=str, default=str(paths.cache_dir() / "record.wav"))
    record.set_defaults(func=cmd_record)

    transcribe = sub.add_parser("transcribe", help="распознать WAV-файл")
    transcribe.add_argument("path")
    transcribe.add_argument("--verbose", action="store_true")
    transcribe.set_defaults(func=cmd_transcribe)

    say = sub.add_parser("say", help="синтезировать русскую речь (SAPI)")
    say.add_argument("text")
    say.add_argument("--out", type=str, default=str(paths.cache_dir() / "say.wav"))
    say.add_argument("--rate", type=int, default=0, help="скорость SAPI: -10..10")
    say.set_defaults(func=cmd_say)

    sub.add_parser("selftest", help="проверка окружения целиком").set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging("WARNING")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
