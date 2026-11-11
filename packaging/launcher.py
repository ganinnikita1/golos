"""Точка входа для собранного приложения (PyInstaller).

Отдельный модуль нужен, чтобы:
* в windowed-сборке не было консоли, но логи всё равно писались в файл;
* перехватывать необработанные исключения и показывать их пользователю.
"""

from __future__ import annotations

import sys
import traceback


def _show_fatal(message: str, detail: str = "") -> None:
    try:
        import ctypes

        text = message + ("\n\n" + detail if detail else "")
        ctypes.windll.user32.MessageBoxW(None, text, "«Голос» — ошибка", 0x10)
    except Exception:
        print(message, detail, file=sys.stderr)


def main() -> int:
    try:
        from golos.app import main as app_main

        return app_main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException:
        detail = traceback.format_exc()
        try:
            from golos.logging_setup import setup

            setup("ERROR", console=False)
            import logging

            logging.getLogger("golos.fatal").exception("Необработанное исключение")
        except Exception:
            pass
        _show_fatal("Приложение не смогло запуститься.", detail[-2500:])
        return 1


if __name__ == "__main__":
    sys.exit(main())
