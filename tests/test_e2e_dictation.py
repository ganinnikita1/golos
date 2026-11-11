"""Сквозной тест диктовки: реальная модель → постобработка → вставка в другое окно.

Требует: скачанную модель, графическую сессию, свободный рабочий стол.
По умолчанию не запускается (маркер slow); явно:
    .venv\\Scripts\\python.exe -m pytest tests/test_e2e_dictation.py -q -s -m "slow and gui"
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "target_window.py"
RU_TTS = ROOT / "tests" / "fixtures" / "ru_tts"
CLIPBOARD_MARKER = "GOLOS-E2E-CLIPBOARD-MARKER"


def _require_fixture() -> None:
    if not (RU_TTS / "01.wav").exists():
        pytest.skip("нет тестовых аудио (tests/fixtures/ru_tts)")


def _focus(hwnd: int) -> None:
    """Выводит окно на передний план.

    Windows блокирует SetForegroundWindow из фонового процесса. Работающий приём —
    присоединиться к input-очереди текущего foreground-потока (AttachThreadInput),
    переключить окно и отсоединиться. Дополнительно «освобождаем» foreground-lock
    имитацией Alt и сразу отпускаем его, чтобы дальнейший Ctrl+V не стал Ctrl+Alt+V.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    SW_RESTORE = 9

    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread) and bool(
        user32.AttachThreadInput(cur_thread, fg_thread, True)
    )
    try:
        user32.AllowSetForegroundWindow(-1)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SwitchToThisWindow(hwnd, True)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_thread, fg_thread, False)

    if int(user32.GetForegroundWindow()) != hwnd:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SwitchToThisWindow(hwnd, True)
        user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)  # Alt не должен остаться зажатым
    time.sleep(0.1)


def _foreground() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow())


@pytest.mark.slow
@pytest.mark.gui
def test_end_to_end_inserts_recognized_text() -> None:
    _require_fixture()
    pytest.importorskip("PySide6")

    from golos.asr.registry import create_engine
    from golos.audio.utils import read_wav
    from golos.config import default_config
    from golos.events import EventBus
    from golos.history import HistoryStore
    from golos.inject import create_injector
    from golos.inject.injector import set_clipboard_text
    from golos.pipeline import DictationPipeline, PipelineHooks

    workdir = Path(tempfile.mkdtemp(prefix="golos-e2e-"))
    out_file = workdir / "target.txt"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target_python = pythonw if pythonw.exists() else Path(sys.executable)

    # Буфер обмена — глобальный ресурс: подменяем его известной меткой, чтобы
    # тест не зависел от пользовательских данных и не печатал их в отчёте.
    try:
        from golos.inject.injector import set_clipboard_text

        set_clipboard_text(CLIPBOARD_MARKER, exclude_from_history=True)
    except Exception:
        pytest.skip("буфер обмена недоступен")

    proc = subprocess.Popen([str(target_python), str(TARGET), str(out_file)])
    try:
        hwnd = 0
        deadline = time.time() + 15
        while time.time() < deadline:
            if out_file.exists():
                parts = out_file.read_text(encoding="utf-8").splitlines()
                if len(parts) >= 2 and parts[1].strip():
                    hwnd = int(parts[1].strip())
                    break
            time.sleep(0.1)
        assert hwnd, "окно-приёмник не запустилось"

        _focus(hwnd)
        deadline = time.time() + 10
        while time.time() < deadline and _foreground() != hwnd:
            time.sleep(0.2)
        assert _foreground() == hwnd, "не удалось перевести фокус на окно-приёмник"
        time.sleep(0.3)

        cfg = default_config()
        cfg.text.trailing_space = False
        audio, rate = read_wav(RU_TTS / "01.wav")
        engine = create_engine(cfg.asr)
        engine.load()

        injector = create_injector("clipboard")
        pipeline = DictationPipeline(
            cfg, EventBus(), HistoryStore(limit=5),
            PipelineHooks(engine_provider=lambda: engine, injector_factory=lambda m: injector),
        )

        # В интерактивной сессии фокус иногда уезжает (другое окно, автозапуск).
        # Реальный пользователь просто повторяет диктовку — тест делает то же.
        typed = ""
        result = None
        elapsed = 0.0
        for attempt in range(3):
            _focus(hwnd)
            deadline = time.time() + 10
            while time.time() < deadline and _foreground() != hwnd:
                time.sleep(0.2)
            if _foreground() != hwnd:
                time.sleep(0.5)
                continue
            try:
                from golos.inject.injector import set_clipboard_text

                set_clipboard_text(CLIPBOARD_MARKER)
            except Exception:
                pass
            started = time.monotonic()
            result = pipeline.run(audio, rate)
            elapsed = time.monotonic() - started
            deadline = time.time() + 5
            while time.time() < deadline:
                if out_file.exists():
                    typed = out_file.read_text(encoding="utf-8").splitlines()[0]
                    if typed:
                        break
                time.sleep(0.1)
            if typed:
                break
            print(f"  попытка {attempt + 1}: вставка не долетела, повторяю")

        assert result is not None, "пайплайн не вернул результат"
        assert result.inserted, "текст не вставлен"
        assert "Привет" in typed, "в окно вставлен не распознанный текст"
        assert "завтра" in typed, "в окно вставлен не распознанный текст"
        assert CLIPBOARD_MARKER not in typed, "вставлен старый буфер — буфер восстановлен слишком рано"
        assert result.asr_elapsed_s < 3.0, f"ASR медленный: {result.asr_elapsed_s:.2f} c"
        assert result.rtf < 0.35, f"RTF {result.rtf:.2f} > 0.35"
        assert elapsed < 6.0, f"полный цикл слишком долгий: {elapsed:.2f} c"

        # После вставки буфер обязан вернуться к прежнему содержимому.
        from golos.inject.injector import get_clipboard_text

        assert get_clipboard_text() == CLIPBOARD_MARKER, "буфер обмена не восстановлен"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
