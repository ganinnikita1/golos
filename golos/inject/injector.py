"""Вставка распознанного текста в активное окно Windows.

Два метода:

* ``clipboard`` (по умолчанию, основной) — сохраняем содержимое буфера обмена
  (все копируемые форматы), кладём текст, шлём Ctrl+V через ``SendInput``,
  затем возвращаем буфер на место. Работает в редакторах, чатах, браузерах
  и терминалах, где посимвольный ввод ненадёжен;
* ``type`` — посимвольный Unicode-ввод через ``SendInput``. Буфер не трогает,
  но медленнее и хуже переносится некоторыми приложениями (игры, RDP).

Всё на ctypes/stdlib. Публичный метод ``inject`` никогда не бросает исключения.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import InsertConfig
from . import _win32

logger = logging.getLogger(__name__)

#: Сколько символов вставляем за один проход буфера (защита от гигантских вставок).
CLIPBOARD_CHUNK = 8000
#: Пауза после Ctrl+V, чтобы приложение успело забрать данные из буфера.
#: 0.35 с — с запасом для загруженной машины и медленных Electron-приложений.
CONSUME_DELAY_S = 0.35
#: Пауза перед восстановлением буфера (некоторые приложения читают буфер лениво).
RESTORE_DELAY_S = 0.15
#: Пауза между чанками длинного текста: приёмник должен успеть обработать Ctrl+V.
CHUNK_GAP_S = 0.2

#: Буфер обмена — глобальный ресурс: вставки сериализуем между потоками.
_CLIPBOARD_LOCK = threading.Lock()


def _prepare(text: str) -> str:
    """Нормализует переводы строк, выкидывает NUL и суррогатные «половинки»."""
    if not isinstance(text, str) or not text:
        return ""
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _split_text(text: str, limit: int = CLIPBOARD_CHUNK) -> list[str]:
    """Режет длинный текст на куски по границам слов, не ломая суррогаты."""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + limit, length)
        if end < length:
            window_start = max(start, end - max(100, limit // 5))
            cut = -1
            for index in range(end, window_start, -1):
                if text[index - 1] in " \n\t":
                    cut = index
                    break
            if cut > start:
                end = cut
            # Не разрываем суррогатную пару (эмодзи) между кусками.
            if end < length and "\ud800" <= text[end - 1] <= "\udbff":
                end -= 1
                if end <= start:  # патологический случай — режем жёстко
                    end = min(start + limit, length)
        chunks.append(text[start:end])
        start = end
    return chunks


class ClipboardInjector:
    """Основной метод: буфер обмена + Ctrl+V c сохранением/восстановлением."""

    #: Пауза между Ctrl+V и восстановлением буфера.
    restore_delay_s = RESTORE_DELAY_S

    def inject(self, text: str, cfg: InsertConfig) -> bool:
        try:
            return self._inject(text, cfg or InsertConfig())
        except Exception:  # никогда не ломаем пайплайн
            logger.exception("Вставка через буфер обмена не удалась")
            return False

    # ------------------------------------------------------------------ #
    def _inject(self, text: str, cfg: InsertConfig) -> bool:
        prepared = _prepare(text)
        if not prepared:
            return True
        paste_delay = max(0.0, float(cfg.paste_delay_ms or 0) / 1000.0)
        with _CLIPBOARD_LOCK:
            foreground = _win32.get_foreground_window()
            snapshot = _win32.snapshot_clipboard() if cfg.restore_clipboard else None
            if cfg.restore_clipboard and snapshot is None:
                logger.warning("Не удалось сохранить буфер обмена — он не будет восстановлен")
            pasted = False
            chunks = _split_text(prepared)
            #: Последний кусок, который реально лежал в буфере (для проверки перед restore).
            last_copied: str | None = None
            try:
                for chunk_index, chunk in enumerate(chunks):
                    if foreground and _win32.get_foreground_window() != foreground:
                        logger.warning(
                            "Фокус сменился во время вставки — отменяю, чтобы не писать "
                            "в чужое окно (ожидалось 0x%X)", foreground,
                        )
                        return False
                    if not _win32.set_clipboard_text(
                        chunk, exclude_from_history=cfg.exclude_from_history
                    ):
                        logger.warning("Буфер обмена недоступен — перехожу на посимвольный ввод")
                        if self._type_fallback("".join(chunks[chunk_index:])):
                            pasted = True
                        break
                    last_copied = chunk
                    time.sleep(paste_delay)
                    if not _win32.press_ctrl_v():
                        logger.warning("SendInput не смог отправить Ctrl+V")
                        break
                    pasted = True
                    if chunk_index + 1 < len(chunks):
                        # Даём приложению обработать вставку, прежде чем менять буфер.
                        time.sleep(CHUNK_GAP_S)
                if pasted:
                    time.sleep(CONSUME_DELAY_S)
            finally:
                self._restore_after_insert(snapshot, last_copied, cfg)
        if pasted and cfg.press_enter_after:
            time.sleep(0.05)
            if not _win32.press_enter():
                logger.warning("Не удалось отправить Enter после вставки")
        return pasted

    @staticmethod
    def _restore_after_insert(
        snapshot: "_win32.ClipboardSnapshot | None",
        last_copied: str | None,
        cfg: InsertConfig,
    ) -> None:
        """Возвращает буфер в исходное состояние, не оставляя в нём диктовку."""
        if last_copied is None:
            return  # буфер нам не принадлежал — трогать нечего
        time.sleep(RESTORE_DELAY_S)
        if not ClipboardInjector._clipboard_still_holds(last_copied):
            # Пользователь или другое приложение уже положили своё —
            # не затираем чужие данные старым снимком.
            logger.info("Буфер обмена изменился извне — восстановление не требуется")
            return
        if not cfg.restore_clipboard:
            # Пользователь сознательно разрешил оставить текст в буфере;
            # он всё равно помечен как приватный и не попадёт в журнал Win+V.
            return
        if snapshot is not None:
            if _win32.restore_clipboard(
                snapshot, exclude_from_history=cfg.exclude_from_history
            ):
                if not ClipboardInjector._clipboard_still_holds(last_copied):
                    return
                logger.warning("Восстановленный буфер всё ещё содержит диктовку")
        # Снимка нет или вернуть не удалось: диктовка не должна остаться в буфере.
        if _win32.clear_clipboard():
            logger.info("Буфер очищен: восстановить прежнее содержимое не удалось")
        else:
            logger.warning("Не удалось ни восстановить, ни очистить буфер обмена")


    @staticmethod
    def _clipboard_still_holds(text: str) -> bool:
        """True, если в буфере всё ещё наш текст (с учётом CRLF в буфере)."""
        try:
            current = _win32.get_clipboard_text()
        except Exception:  # pragma: no cover - защита от неожиданностей
            return False
        if current is None:
            return False
        expected = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        return current == expected

    @staticmethod
    def _type_fallback(text: str) -> bool:
        try:
            return _win32.send_unicode_text(_prepare(text), chunk=8, delay=0.0015)
        except Exception:  # pragma: no cover - защита от неожиданностей
            logger.exception("Резервный посимвольный ввод не удался")
            return False

    # ------------------------------------------------------------------ #
    # Сервисные методы для UI/тестов
    # ------------------------------------------------------------------ #
    @staticmethod
    def copy_to_clipboard(text: str) -> bool:
        """Кладёт текст в буфер, не трогая активное окно (для «Копировать» в UI)."""
        return _win32.set_clipboard_text(_prepare(text))

    @staticmethod
    def read_clipboard() -> str | None:
        return _win32.get_clipboard_text()


class TypeInjector:
    """Резервный метод: посимвольный Unicode-ввод через SendInput."""

    #: Сколько символов отправляем между короткими паузами.
    TYPE_CHUNK = 2000

    def inject(self, text: str, cfg: InsertConfig) -> bool:
        try:
            return self._inject(text, cfg or InsertConfig())
        except Exception:
            logger.exception("Посимвольная вставка не удалась")
            return False

    def _inject(self, text: str, cfg: InsertConfig) -> bool:
        prepared = _prepare(text)
        if not prepared:
            return True
        ok = True
        for chunk in _split_text(prepared, self.TYPE_CHUNK):
            ok = _win32.send_unicode_text(chunk, chunk=8, delay=0.0015)
            if not ok:
                logger.warning("SendInput не смог напечатать часть текста")
                break
        if ok and cfg.press_enter_after:
            time.sleep(0.05)
            _win32.press_enter()
        return ok


def create_injector(method: str = "clipboard") -> ClipboardInjector | TypeInjector:
    """Фабрика вставщиков: ``clipboard`` (по умолчанию) или ``type``."""
    normalized = (method or "").strip().lower()
    if normalized == "type":
        return TypeInjector()
    if normalized not in ("", "clipboard"):
        logger.warning("Неизвестный метод вставки %r — использую clipboard", method)
    return ClipboardInjector()


#: Удобные реэкспорты для UI и тестов.
set_clipboard_text = _win32.set_clipboard_text
get_clipboard_text = _win32.get_clipboard_text
get_foreground_window = _win32.get_foreground_window
