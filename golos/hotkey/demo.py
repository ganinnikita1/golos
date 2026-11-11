"""Демонстрация глобальной горячей клавиши: события печатаются в консоль.

Запуск::

    .venv\\Scripts\\python.exe -m golos.hotkey.demo
    .venv\\Scripts\\python.exe -m golos.hotkey.demo --hotkey f9 --mode push_to_talk

Нажатие хоткея печатает ``START``/``STOP``, Esc во время «записи» — ``CANCEL``.
Хоткей глотается: активное приложение не должно его получать (проверьте:
поставьте курсор в Блокнот и нажмите комбинацию — в тексте не появится пробел).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from ..config import default_config
from .hook import create_listener, parse_hotkey

logger = logging.getLogger("golos.hotkey.demo")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Демо хука горячей клавиши «Голос»")
    parser.add_argument("--hotkey", help="спецификация, например ctrl+alt+space или f9")
    parser.add_argument("--mode", choices=("toggle", "push_to_talk"), help="режим срабатывания")
    parser.add_argument("--cancel", help="клавиша отмены, по умолчанию esc")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = default_config().hotkey
    if args.hotkey:
        cfg.hotkey = args.hotkey
    if args.mode:
        cfg.mode = args.mode
    if args.cancel:
        cfg.cancel_key = args.cancel

    try:
        parsed = parse_hotkey(cfg.hotkey)
    except ValueError as exc:
        logger.error("Некорректная горячая клавиша: %s", exc)
        return 2
    logger.info("Хоткей: %s → %s, режим: %s, отмена: %s",
                cfg.hotkey, sorted(parsed), cfg.mode, cfg.cancel_key or "нет")

    state = {"active": False}

    def on_start() -> None:
        state["active"] = True
        logger.info("START (запись пошла)")

    def on_stop() -> None:
        state["active"] = False
        logger.info("STOP (запись остановлена)")

    def on_cancel() -> None:
        state["active"] = False
        logger.info("CANCEL (запись отменена)")

    listener = create_listener(
        cfg,
        on_start=on_start,
        on_stop=on_stop,
        on_cancel=on_cancel,
        state_getter=lambda: state["active"],
    )
    try:
        listener.start()
    except Exception as exc:
        logger.error("Не удалось поставить хук: %s", exc)
        return 1
    logger.info("Хук установлен. Нажмите %s. Ctrl+C — выход.", cfg.hotkey)
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("Выход по Ctrl+C")
    finally:
        listener.stop()
        logger.info("Хук снят, потоки завершены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
