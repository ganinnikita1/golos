"""Ядро приложения: жизненный цикл, потоки, горячая клавиша, интеграция UI.

Потоковая модель:
* главный поток — Qt (UI), только он трогает виджеты;
* worker-поток — распознавание/полировка/вставка (долгие операции);
* поток хука клавиатуры — приходит из golos.hotkey;
* колбэк PortAudio — приходит из sounddevice (уровни сигнала).

Связь между ними — golos.events.EventBus (потокобезопасная очередь) + Controller API
для вызовов из UI.
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import APP_NAME, __version__, paths
from .audio.recorder import AudioError, Recorder
from .audio.sounds import play_error, play_start, play_stop
from .config import AppConfig, load_config, save_config
from .controller import DeviceInfo, HistoryItem
from .events import EventBus
from .history import HistoryStore
from .logging_setup import get_logger, setup as setup_logging
from .pipeline import DictationPipeline, PipelineHooks, SessionState

logger = get_logger(__name__)

SINGLE_INSTANCE_MUTEX = "Global\\GolosDictationSingleInstance"
LOCAL_SERVER_NAME = "GolosDictationIPC"


# --------------------------------------------------------------------------- #
# Автозапуск
# --------------------------------------------------------------------------- #
def autostart_command() -> str:
    """Команда для HKCU\\...\\Run."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" -m golos --background'


def set_autostart(enabled: bool) -> bool:
    """Прописывает/удаляет ключ автозапуска. True при успехе."""
    try:
        import winreg  # noqa: PLC0415

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
                logger.info("Автозапуск включён: %s", autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    logger.info("Автозапуск выключен")
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        logger.exception("Не удалось изменить автозапуск")
        return False


def autostart_enabled() -> bool:
    try:
        import winreg  # noqa: PLC0415

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def configure_hf_cache() -> None:
    """Держим кэш HuggingFace внутри данных приложения.

    Иначе transformers/huggingface_hub создают ~/.cache/huggingface, что на
    read-only профилях падает, а на нормальных — мусорит в чужом месте.
    Пользовательский HF_HOME, если задан явно, уважаем.
    """
    cache = paths.cache_dir() / "huggingface"
    try:
        (cache / "hub").mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    os.environ.setdefault("HF_HOME", str(cache))
    os.environ.setdefault("HF_HUB_CACHE", str(cache / "hub"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache / "hub"))
    # Мы скачиваем модель сами; офлайн-режим не включаем, но сеть не дергаем без нужды.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


# --------------------------------------------------------------------------- #
# Движок распознавания: ленивое создание + предзагрузка
# --------------------------------------------------------------------------- #
class EngineManager:
    def __init__(self, cfg: AppConfig, bus: EventBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self._engine: Any = None
        self._lock = threading.RLock()
        self._loading = False
        self._signature = self._make_signature(cfg)
        self._span: float = 0.0

    def _make_signature(self, cfg: AppConfig) -> tuple:
        asr = cfg.asr
        return (
            asr.engine, asr.gigaam_repo, asr.gigaam_variant, asr.whisper_model,
            asr.whisper_compute_type, asr.cloud_provider, asr.cloud_base_url, asr.cloud_model,
        )

    @property
    def engine(self) -> Any:
        with self._lock:
            if self._engine is None:
                self._engine = self._create()
            return self._engine

    def _create(self) -> Any:
        try:
            from .asr.registry import create_engine  # noqa: PLC0415

            engine = create_engine(self.cfg.asr, models_dir=paths.models_dir())
            logger.info("Создан движок распознавания: %s", getattr(engine, "name", "?"))
            return engine
        except Exception:
            logger.exception("Не удалось создать движок распознавания")
            return None

    def progress(self, message: str, fraction: float | None) -> None:
        self.bus.emit("progress", message=message, fraction=fraction)

    def load_async(self) -> None:
        threading.Thread(target=self.load_sync, name="engine-loader", daemon=True).start()

    def load_sync(self) -> bool:
        with self._lock:
            if self._loading:
                return False
            if self._engine is not None and self._engine.is_loaded():
                return True
            self._loading = True
        started = time.monotonic()
        try:
            engine = self.engine
            if engine is None:
                self.bus.emit("error", message="Не удалось создать движок распознавания",
                              detail="см. журнал")
                return False
            self.bus.emit("state", state=SessionState.LOADING)
            engine.load(self.progress)
            self._span = time.monotonic() - started
            logger.info("Модель загружена за %.2f c", self._span)
            self.bus.emit("toast", message="Модель распознавания готова", level="info")
            return True
        except Exception as exc:
            logger.exception("Загрузка модели не удалась")
            self.bus.emit("error", message="Не удалось загрузить модель распознавания", detail=str(exc))
            return False
        finally:
            with self._lock:
                self._loading = False
                self.bus.emit("state", state=SessionState.IDLE)

    def reset_if_changed(self, cfg: AppConfig) -> bool:
        """Сбрасывает движок, если сменились настройки распознавания."""
        signature = self._make_signature(cfg)
        with self._lock:
            if signature == self._signature and self._engine is not None:
                return False
            old = self._engine
            self._engine = None
            self._signature = signature
        if old is not None:
            try:
                old.unload()
            except Exception:
                logger.exception("Ошибка выгрузки движка")
        return True

    def unload(self) -> None:
        with self._lock:
            engine = self._engine
            self._engine = None
        if engine is not None:
            try:
                engine.unload()
            except Exception:
                pass

    def status(self) -> dict:
        engine = self._engine
        return {
            "engine": getattr(engine, "name", self.cfg.asr.engine) or self.cfg.asr.engine,
            "loaded": bool(engine is not None and engine.is_loaded()),
            "loading": self._loading,
            "message": "Модель загружена" if engine is not None and engine.is_loaded() else "Модель не загружена",
        }

    def installed(self) -> bool:
        """Установлена ли модель на диск (без загрузки и без сети)."""
        try:
            from .asr.registry import engine_is_installed

            return bool(engine_is_installed(self.cfg.asr))
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# Приложение
# --------------------------------------------------------------------------- #
class DictationApp:
    """Реализует golos.controller.Controller и владеет всеми потоками."""

    def __init__(self, cfg: AppConfig | None = None, *, with_ui: bool = True,
                 background: bool = False) -> None:
        paths.ensure_dirs()
        configure_hf_cache()
        self.cfg = cfg or load_config()
        setup_logging(self.cfg.log_level)
        logger.info("Старт %s v%s (frozen=%s)", APP_NAME, __version__, getattr(sys, "frozen", False))

        self.bus = EventBus()
        self.history = HistoryStore(limit=self.cfg.history_limit)
        self.recorder = Recorder(self.cfg.audio, self.bus, on_auto_stop=self._on_auto_stop)
        self.engines = EngineManager(self.cfg, self.bus)
        self._engine_lock = threading.RLock()

        self.pipeline = DictationPipeline(
            self.cfg,
            self.bus,
            self.history,
            PipelineHooks(
                engine_provider=self._engine_provider,
                injector_factory=self._make_injector,
                polisher_factory=self._make_polisher,
                insert=True,
                on_result=self._on_result,
            ),
        )

        self._job_queue: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, name="dictation-worker", daemon=True)
        self._worker.start()
        self._hotkey: Any = None
        self._hotkey_lock = threading.RLock()
        self._quitting = threading.Event()
        self._ui: Any = None
        self._with_ui = with_ui
        self._background = background
        self._injector_cache: dict[str, Any] = {}
        self._mic_test_thread: threading.Thread | None = None
        self._audio_error_shown = False

        self._sync_autostart()
        self._start_hotkey()

    # ------------------------------------------------------------------ #
    # Запуск
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        if self._with_ui:
            return self._run_with_ui()
        return self._run_headless()

    def _run_with_ui(self) -> int:
        from PySide6.QtCore import QTimer  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationDisplayName("«Голос»")
        app.setQuitOnLastWindowClosed(False)

        if not self._acquire_single_instance():
            logger.info("Приложение уже запущено — открываю настройки существующего экземпляра")
            self._notify_existing_instance()
            return 0

        try:
            from .ui.app_ui import GolosUI  # noqa: PLC0415

            self._ui = GolosUI(self)
            self._ui.start()
        except Exception:
            logger.exception("Не удалось запустить UI")
            self.bus.emit("error", message="Не удалось запустить интерфейс", detail=traceback.format_exc())

        # Предзагрузка модели после появления трея (не блокирует старт).
        # До мастера первого запуска модель не качаем: пользователь ещё не дал согласие.
        if self.cfg.onboarding_done or self.engines.installed():
            QTimer.singleShot(700, self.engines.load_async)
        if not self._background:
            QTimer.singleShot(900, self._maybe_show_onboarding)
        QTimer.singleShot(1200, self._start_ipc_server)
        app.aboutToQuit.connect(self._on_qt_quit)
        logger.info("UI запущен")
        return app.exec()

    def _run_headless(self) -> int:
        if not self._acquire_single_instance():
            print("«Голос» уже запущен.")
            return 0
        logger.info("Headless-режим: Ctrl+C для выхода")
        self.engines.load_async()
        try:
            while not self._quitting.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        self.quit()
        return 0

    def _maybe_show_onboarding(self) -> None:
        if not self.cfg.onboarding_done and self._ui is not None:
            try:
                self._ui.show_onboarding()
            except Exception:
                logger.exception("Не удалось показать мастер первого запуска")

    # ------------------------------------------------------------------ #
    # Single instance + IPC
    # ------------------------------------------------------------------ #
    def _acquire_single_instance(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            self._mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
            if not self._mutex:
                return True
            already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
            if already:
                kernel32.CloseHandle(self._mutex)
                self._mutex = None
                return False
            return True
        except Exception:
            logger.exception("Не удалось проверить единственность экземпляра")
            return True

    def _notify_existing_instance(self) -> None:
        try:
            from PySide6.QtNetwork import QLocalSocket  # noqa: PLC0415

            socket = QLocalSocket()
            socket.connectToServer(LOCAL_SERVER_NAME)
            if socket.waitForConnected(600):
                socket.write(b"show-settings\n")
                socket.flush()
                socket.waitForBytesWritten(600)
                socket.disconnectFromServer()
        except Exception:
            logger.exception("Не удалось передать команду запущенному экземпляру")

    def _start_ipc_server(self) -> None:
        try:
            from PySide6.QtNetwork import QLocalServer  # noqa: PLC0415

            QLocalServer.removeServer(LOCAL_SERVER_NAME)
            self._server = QLocalServer()
            if not self._server.listen(LOCAL_SERVER_NAME):
                logger.warning("IPC-сервер не запустился: %s", self._server.errorString())
                return

            def on_connection() -> None:
                conn = self._server.nextPendingConnection()
                if conn is None:
                    return

                def on_ready() -> None:
                    try:
                        command = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
                        if command == "show-settings" and self._ui is not None:
                            self._ui.show_settings()
                    finally:
                        conn.disconnectFromServer()

                conn.readyRead.connect(on_ready)
                conn.disconnected.connect(conn.deleteLater)

            self._server.newConnection.connect(on_connection)
        except Exception:
            logger.exception("IPC-сервер не поднялся")

    # ------------------------------------------------------------------ #
    # Горячая клавиша
    # ------------------------------------------------------------------ #
    def _start_hotkey(self) -> None:
        self._stop_hotkey()
        try:
            from .hotkey import create_listener  # noqa: PLC0415

            listener = create_listener(
                self.cfg.hotkey,
                on_start=self.start_recording,
                on_stop=self.stop_recording,
                on_cancel=self.cancel,
                state_getter=self._is_dictation_active,
            )
            listener.start()
            self._hotkey = listener
            logger.info("Хоткей активен: %s (%s)", self.cfg.hotkey.hotkey, self.cfg.hotkey.mode)
        except Exception as exc:
            logger.exception("Не удалось включить горячую клавишу")
            self.bus.emit("error", message="Не удалось включить горячую клавишу", detail=str(exc))

    def _stop_hotkey(self) -> None:
        with self._hotkey_lock:
            listener = self._hotkey
            self._hotkey = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.exception("Ошибка остановки хука")

    def _is_dictation_active(self) -> bool:
        return self.recorder.is_recording or self.pipeline.state != SessionState.IDLE

    # ------------------------------------------------------------------ #
    # Запись
    # ------------------------------------------------------------------ #
    def start_recording(self) -> None:
        if self._is_dictation_active():
            logger.debug("Игнорирую start: уже идёт запись/распознавание")
            return
        if not self.engines.status()["loaded"] and not self.engines._loading:
            self.engines.load_async()
        try:
            self.recorder.start()
        except AudioError as exc:
            self.bus.emit("error", message=str(exc))
            if self.cfg.ui.sounds:
                play_error()
            return
        except Exception as exc:
            logger.exception("Ошибка старта записи")
            self.bus.emit("error", message="Не удалось начать запись", detail=str(exc))
            return
        if self.cfg.ui.sounds:
            play_start()
        self.bus.emit("state", state=SessionState.RECORDING)

    def _on_auto_stop(self) -> None:
        logger.info("Достигнут лимит длительности записи — останавливаю")
        self.stop_recording()

    def stop_recording(self) -> None:
        if not self.recorder.is_recording:
            logger.debug("Игнорирую stop: запись не идёт")
            return
        audio = self.recorder.stop()
        if self.cfg.ui.sounds:
            play_stop()
        if audio.size == 0:
            self.bus.emit("toast", message="Запись пустая", level="warn")
            self.bus.emit("state", state=SessionState.IDLE)
            return
        self._job_queue.put((audio, self.cfg.audio.sample_rate))

    def cancel(self) -> None:
        if self.recorder.is_recording:
            self.recorder.cancel()
            self.bus.emit("toast", message="Запись отменена", level="info")
        else:
            self.bus.emit("toast", message="Нечего отменять", level="info")
        self.bus.emit("state", state=SessionState.IDLE)

    def is_busy(self) -> bool:
        return self._is_dictation_active()

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    def _worker_loop(self) -> None:
        logger.debug("Worker-поток запущен")
        while True:
            try:
                job = self._job_queue.get(timeout=0.5)
            except queue.Empty:
                if self._quitting.is_set():
                    break
                continue
            if job is None:
                break
            audio, sample_rate = job
            try:
                self.pipeline.run(audio, sample_rate)
            except Exception:
                logger.exception("Необработанная ошибка пайплайна")
                self.bus.emit("error", message="Внутренняя ошибка обработки записи")
                self.bus.emit("state", state=SessionState.IDLE)
        logger.debug("Worker-поток завершён")

    def _on_result(self, result) -> None:  # noqa: ANN001
        logger.info(
            "Готово: %d симв., %.2f c аудио, ASR %.2f c, всего %.2f c, RTF %.2f, вставка=%s",
            len(result.text), result.duration_s, result.asr_elapsed_s,
            result.total_elapsed_s, result.rtf, result.inserted,
        )

    # ------------------------------------------------------------------ #
    # Движок/инжектор/полировщик
    # ------------------------------------------------------------------ #
    def _engine_provider(self):  # noqa: ANN201
        with self._engine_lock:
            return self.engines.engine

    def _make_injector(self, method: str):
        cached = self._injector_cache.get(method)
        if cached is not None:
            return cached
        try:
            from .inject import create_injector  # noqa: PLC0415

            injector = create_injector(method)
            self._injector_cache[method] = injector
            return injector
        except ModuleNotFoundError:
            logger.warning("Модуль вставки текста отсутствует")
            return None
        except Exception:
            logger.exception("Не удалось создать инжектор")
            return None

    def _make_polisher(self):
        try:
            from .text.llm import LLMPolisher  # noqa: PLC0415

            return LLMPolisher(self.cfg.polish)
        except ModuleNotFoundError:
            return None
        except Exception:
            logger.exception("Не удалось создать LLM-полировщик")
            return None

    def load_engine(self) -> None:
        self.engines.load_async()

    def engine_status(self) -> dict:
        return self.engines.status()

    # ------------------------------------------------------------------ #
    # Controller API
    # ------------------------------------------------------------------ #
    def get_config(self) -> AppConfig:
        return self.cfg

    def apply_config(self, cfg: AppConfig, *, save: bool = True) -> None:
        old = self.cfg
        self.cfg = cfg
        if save:
            save_config(cfg)
        self.pipeline.cfg = cfg
        # Движок должен видеть те же настройки ASR, что и остальное приложение.
        self.engines.cfg = cfg
        self.recorder.cfg = cfg.audio
        self._injector_cache.clear()
        if self.engines.reset_if_changed(cfg):
            logger.info("Настройки ASR изменились — движок будет загружен заново")
            if not self._background:
                self.engines.load_async()
        hotkey_changed = (
            old.hotkey.hotkey != cfg.hotkey.hotkey
            or old.hotkey.mode != cfg.hotkey.mode
            or old.hotkey.cancel_key != cfg.hotkey.cancel_key
        )
        if hotkey_changed:
            self._start_hotkey()
        if old.ui.autostart != cfg.ui.autostart:
            set_autostart(cfg.ui.autostart)
        self.bus.emit("toast", message="Настройки сохранены", level="info")

    def _sync_autostart(self) -> None:
        actual = autostart_enabled()
        if self.cfg.ui.autostart != actual:
            logger.info("Синхронизирую автозапуск: %s -> %s", actual, self.cfg.ui.autostart)
            set_autostart(self.cfg.ui.autostart)

    def get_devices(self) -> list[DeviceInfo]:
        try:
            raw = self.recorder.devices()
        except AudioError as exc:
            self.bus.emit("error", message=str(exc))
            return []
        except Exception:
            logger.exception("Не удалось получить устройства")
            return []
        return [
            DeviceInfo(index=int(item["index"]), name=str(item["name"]),
                       is_default=bool(item.get("is_default")), channels=int(item.get("channels", 1)))
            for item in raw
        ]

    def test_microphone(self, seconds: float = 3.0) -> None:
        if self._mic_test_thread is not None and self._mic_test_thread.is_alive():
            return

        def worker() -> None:
            was_recording = self.recorder.is_recording
            if was_recording:
                return
            try:
                self.recorder.start()
            except AudioError as exc:
                self.bus.emit("error", message=str(exc))
                return
            peak = 0.0
            started = time.monotonic()
            while time.monotonic() - started < seconds and not self._quitting.is_set():
                time.sleep(0.1)
                level = getattr(self.recorder, "_peak_level", 0.0)
                peak = max(peak, level)
            audio = self.recorder.stop()
            duration = audio.shape[0] / float(self.cfg.audio.sample_rate) if audio.size else 0.0
            if peak < 0.02:
                self.bus.emit("toast", message="Микрофон почти не слышит — проверьте устройство",
                              level="warn")
            else:
                self.bus.emit("toast", message=f"Микрофон работает: записано {duration:.1f} c",
                              level="info")
            self.bus.emit("level", value=0.0)

        self._mic_test_thread = threading.Thread(target=worker, name="mic-test", daemon=True)
        self._mic_test_thread.start()

    def history_items(self) -> list[HistoryItem]:
        return self.history.items()

    def history(self) -> list[HistoryItem]:
        return self.history.items()

    def clear_history(self) -> None:
        self.history.clear()
        self.bus.emit("toast", message="История очищена", level="info")

    def insert_text(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        injector = self._make_injector(self.cfg.insert.method)
        if injector is None:
            return False
        try:
            return bool(injector.inject(text + (" " if self.cfg.text.trailing_space else ""),
                                        self.cfg.insert))
        except Exception:
            logger.exception("Вставка текста не удалась")
            return False

    def copy_to_clipboard(self, text: str) -> None:
        try:
            from .inject.injector import set_clipboard_text  # noqa: PLC0415

            set_clipboard_text(text)
        except Exception:
            logger.exception("Не удалось скопировать в буфер обмена")

    def open_config_dir(self) -> None:
        try:
            os.startfile(str(paths.appdata_dir()))  # noqa: S606
        except OSError:
            logger.exception("Не удалось открыть папку конфига")

    def open_logs_dir(self) -> None:
        try:
            os.startfile(str(paths.logs_dir()))  # noqa: S606
        except OSError:
            logger.exception("Не удалось открыть папку логов")

    def mark_onboarding_done(self) -> None:
        self.cfg.onboarding_done = True
        save_config(self.cfg)

    def show_settings(self) -> None:
        if self._ui is not None:
            try:
                self._ui.show_settings()
            except Exception:
                logger.exception("Не удалось открыть настройки")

    def show_history(self) -> None:
        if self._ui is not None:
            try:
                self._ui.show_history()
            except Exception:
                logger.exception("Не удалось открыть историю")

    def quit(self) -> None:
        if self._quitting.is_set():
            return
        self._quitting.set()
        logger.info("Завершение работы")
        try:
            self.recorder.close()
        except Exception:
            pass
        self._stop_hotkey()
        self._job_queue.put(None)
        self._worker.join(timeout=3.0)
        self.engines.unload()
        try:
            if self._mutex:
                ctypes.windll.kernel32.CloseHandle(self._mutex)
                self._mutex = None
        except Exception:
            pass

    def _on_qt_quit(self) -> None:
        self.quit()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="golos", description="«Голос» — диктовка для Windows")
    parser.add_argument("--background", action="store_true", help="запуск свёрнутым в трей")
    parser.add_argument("--no-ui", action="store_true", help="без интерфейса (для тестов)")
    parser.add_argument("--config", type=str, default=None, help="путь к файлу конфигурации")
    parser.add_argument("--diagnose", action="store_true",
                        help="напечатать пути, состояние модели и логов, затем выйти")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.diagnose:
        return run_diagnostics(args.config)

    cfg = load_config(Path(args.config)) if args.config else load_config()
    app = DictationApp(cfg, with_ui=not args.no_ui, background=args.background)
    try:
        return app.run()
    except Exception:
        logger.exception("Критическая ошибка приложения")
        return 1


def run_diagnostics(config_file: str | None = None) -> int:
    """Текстовый отчёт для поддержки: пути, права, модель, логи."""
    import json

    from .asr.registry import engine_info, engine_is_installed

    paths.ensure_dirs()
    cfg = load_config(Path(config_file)) if config_file else load_config()
    log_path = setup_logging(cfg.log_level, console=True)
    report: dict[str, Any] = {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        # Пути обезличены: имя учётной записи не должно попадать в публикуемые отчёты.
        "paths": {key: paths.display_path(value) for key, value in paths.describe().items()},
        "config": paths.display_path(paths.config_path()),
        "log": paths.display_path(log_path) if log_path else None,
        "engine": cfg.asr.engine,
        "gigaam_variant": cfg.asr.gigaam_variant,
        "model_installed": engine_is_installed(cfg.asr),
        "model_info": engine_info(cfg.asr),
        "devices": [],
    }
    try:
        report["devices"] = [item["name"] for item in Recorder(cfg.audio).devices()]
    except Exception as exc:  # noqa: BLE001
        report["devices_error"] = str(exc)
    report["model_info"] = {
        key: (paths.display_path(value) if key == "model_dir" else value)
        for key, value in dict(report["model_info"]).items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0



if __name__ == "__main__":
    sys.exit(main())
