"""Тонкие обёртки Win32 API через ctypes: буфер обмена, SendInput, окна.

Модуль намеренно не зависит ни от чего, кроме stdlib: pywin32/pyperclip/keyboard
в проекте запрещены. Всё, что делает система для чужого приложения, живёт здесь,
чтобы ``injector.py`` и ``hotkey`` оставались читаемыми.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------- #
# Clipboard formats
# --------------------------------------------------------------------------- #
CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_OEMTEXT = 7
CF_DIB = 8
CF_PALETTE = 9
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_LOCALE = 16
CF_DIBV5 = 17
CF_OWNERDISPLAY = 0x0080
CF_DSPTEXT = 0x0081
CF_DSPBITMAP = 0x0082
CF_DSPMETAFILEPICT = 0x0083
CF_DSPENHMETAFILE = 0x008E
CF_GDIOBJFIRST = 0x0300
CF_GDIOBJLAST = 0x03FF
CF_PRIVATEFIRST = 0x0200
CF_PRIVATELAST = 0x02FF

#: Форматы, чьи данные — не HGLOBAL (GDI-хендлы, owner-display), их не копируем.
_NON_GLOBAL_FORMATS = frozenset(
    {
        CF_BITMAP,
        CF_METAFILEPICT,
        CF_PALETTE,
        CF_ENHMETAFILE,
        CF_OWNERDISPLAY,
        CF_DSPTEXT,
        CF_DSPBITMAP,
        CF_DSPMETAFILEPICT,
        CF_DSPENHMETAFILE,
    }
)

GMEM_MOVEABLE = 0x0002

# --------------------------------------------------------------------------- #
# Прототипы
# --------------------------------------------------------------------------- #
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
user32.GetClipboardFormatNameW.restype = ctypes.c_int
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


# --------------------------------------------------------------------------- #
# Буфер обмена
# --------------------------------------------------------------------------- #
def open_clipboard(attempts: int = 12, delay: float = 0.01) -> bool:
    """OpenClipboard с повторами: буфер может держать другое приложение."""
    for attempt in range(max(1, attempts)):
        if user32.OpenClipboard(None):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    logger.debug("OpenClipboard не удался (err=%s)", ctypes.get_last_error())
    return False


def close_clipboard() -> None:
    user32.CloseClipboard()


def _format_name(fmt: int) -> str | None:
    if fmt < 0xC000:  # стандартные форматы не имеют имени
        return None
    buffer = ctypes.create_unicode_buffer(256)
    length = user32.GetClipboardFormatNameW(fmt, buffer, len(buffer))
    return buffer.value if length > 0 else None


def _is_copyable_format(fmt: int) -> bool:
    if fmt in _NON_GLOBAL_FORMATS:
        return False
    if CF_GDIOBJFIRST <= fmt <= CF_GDIOBJLAST:
        return False
    if CF_PRIVATEFIRST <= fmt <= CF_PRIVATELAST:
        return False
    return True


def _read_format(fmt: int) -> bytes | None:
    handle = user32.GetClipboardData(fmt)
    if not handle:
        return None
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return None
    try:
        size = kernel32.GlobalSize(handle)
        if not size:
            return None
        return ctypes.string_at(pointer, size)
    finally:
        kernel32.GlobalUnlock(handle)


@dataclass
class ClipboardItem:
    fmt: int
    name: str | None
    data: bytes


@dataclass
class ClipboardSnapshot:
    items: list[ClipboardItem] = field(default_factory=list)


def snapshot_clipboard() -> ClipboardSnapshot | None:
    """Снимает копию всех форматов буфера. ``None`` — не удалось открыть буфер."""
    if not open_clipboard():
        return None
    try:
        items: list[ClipboardItem] = []
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if not _is_copyable_format(fmt):
                continue
            data = _read_format(fmt)
            if data is None:
                continue
            name = _format_name(fmt)
            items.append(ClipboardItem(fmt=fmt, name=name, data=data))
        return ClipboardSnapshot(items=items)
    finally:
        close_clipboard()


def restore_clipboard(
    snapshot: ClipboardSnapshot | None,
    attempts: int = 10,
    *,
    exclude_from_history: bool = False,
) -> bool:
    """Возвращает буфер к состоянию снимка. False — не удалось.

    ``exclude_from_history`` помечает восстановленное содержимое как приватное,
    чтобы возврат буфера не создавал лишнюю запись в журнале Win+V.
    """
    if snapshot is None:
        return False
    for attempt in range(max(1, attempts)):
        if open_clipboard(attempts=1):
            break
        time.sleep(0.03)
    else:
        logger.warning("Не удалось восстановить буфер обмена: он занят другим приложением")
        return False
    try:
        if not user32.EmptyClipboard():
            logger.warning("EmptyClipboard не удался (err=%s)", ctypes.get_last_error())
            return False
        restored = 0
        for item in snapshot.items:
            fmt = item.fmt
            if item.name is not None:
                registered = user32.RegisterClipboardFormatW(item.name)
                if registered:
                    fmt = registered
            if not item.data:
                continue
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(item.data))
            if not handle:
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                continue
            ctypes.memmove(pointer, item.data, len(item.data))
            kernel32.GlobalUnlock(handle)
            if user32.SetClipboardData(fmt, handle):
                restored += 1
            else:
                # Владелец данных не сменился — память всё ещё наша, освобождаем.
                logger.debug("SetClipboardData(%s) не удался при восстановлении", fmt)
                kernel32.GlobalFree(handle)
        logger.debug("Буфер восстановлен: %d/%d форматов", restored, len(snapshot.items))
        if exclude_from_history:
            mark_clipboard_private()
        return True
    finally:
        close_clipboard()


def set_clipboard_text(text: str, *, exclude_from_history: bool = False) -> bool:
    """Кладёт текст в буфер обмена (CF_UNICODETEXT, переводы строк — CRLF).

    ``exclude_from_history`` помечает содержимое служебными форматами Windows,
    чтобы оно не попало в журнал буфера обмена (Win+V) и в облачную синхронизацию.
    """
    prepared = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    payload = prepared.encode("utf-16-le") + b"\x00\x00"
    if not open_clipboard():
        logger.warning("Не удалось открыть буфер обмена для записи")
        return False
    try:
        if not user32.EmptyClipboard():
            logger.warning("EmptyClipboard не удался (err=%s)", ctypes.get_last_error())
            return False
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            logger.warning("SetClipboardData не удался (err=%s)", ctypes.get_last_error())
            return False
        if exclude_from_history:
            mark_clipboard_private()
        return True
    finally:
        close_clipboard()


# --------------------------------------------------------------------------- #
# Приватность буфера: журнал Win+V и облачная синхронизация
# --------------------------------------------------------------------------- #
#: Служебные форматы Windows 10 1809+. Значение DWORD 0 означает «не сохранять».
FORMAT_CAN_INCLUDE_HISTORY = "CanIncludeInClipboardHistory"
FORMAT_CAN_UPLOAD_CLOUD = "CanUploadToCloudClipboard"
FORMAT_EXCLUDE_MONITOR = "ExcludeClipboardContentFromMonitorProcessing"


def _set_dword_format(fmt: int, value: int = 0) -> bool:
    """Кладёт DWORD в буфер (буфер должен быть уже открыт нами)."""
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(pointer, ctypes.byref(wintypes.DWORD(value)), 4)
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def mark_clipboard_private() -> bool:
    """Помечает текущее содержимое буфера как приватное.

    Windows не добавляет такое содержимое в журнал буфера обмена (Win+V)
    и не отправляет в облако. Вызывать при открытом буфере обмена.
    Возвращает True, если хотя бы один формат удалось выставить.
    """
    ok = False
    for name in (FORMAT_CAN_INCLUDE_HISTORY, FORMAT_CAN_UPLOAD_CLOUD, FORMAT_EXCLUDE_MONITOR):
        try:
            fmt = user32.RegisterClipboardFormatW(name)
        except Exception:  # pragma: no cover - зависит от окружения
            fmt = 0
        if fmt and _set_dword_format(fmt, 0):
            ok = True
    return ok


def clear_clipboard() -> bool:
    """Полностью очищает буфер обмена (например, если восстановить не удалось)."""
    if not open_clipboard():
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        close_clipboard()


def get_clipboard_text() -> str | None:
    """Читает текст из буфера обмена. ``None`` — текста нет/буфер недоступен."""
    if not open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            size = kernel32.GlobalSize(handle)
            if not size:
                return None
            raw = ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(handle)
        text = raw.decode("utf-16-le", errors="replace")
        return text.split("\x00", 1)[0]
    finally:
        close_clipboard()


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


# --------------------------------------------------------------------------- #
# SendInput: клавиатура
# --------------------------------------------------------------------------- #
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT


def _scan_code(vk: int) -> int:
    try:
        return int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFFFF
    except Exception:  # pragma: no cover - MapVirtualKey есть всегда
        return 0


def keyboard_input(vk: int, *, key_up: bool = False, extended: bool = False,
                   unicode: bool = False, scan: int = 0) -> INPUT:
    flags = 0
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if unicode:
        flags |= KEYEVENTF_UNICODE
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(
        wVk=0 if unicode else vk,
        wScan=scan,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return item


def send_inputs(inputs: list[INPUT]) -> bool:
    """Отправляет набор событий. False — система отклонила часть ввода."""
    if not inputs:
        return True
    array = (INPUT * len(inputs))(*inputs)
    sent = int(user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) or 0)
    if sent != len(inputs):
        logger.warning("SendInput отправил %d из %d событий (err=%s)",
                       sent, len(inputs), ctypes.get_last_error())
        return False
    return True


def key_down(vk: int, *, extended: bool = False) -> INPUT:
    return keyboard_input(vk, extended=extended, scan=_scan_code(vk))


def key_up(vk: int, *, extended: bool = False) -> INPUT:
    return keyboard_input(vk, key_up=True, extended=extended, scan=_scan_code(vk))


def press_key(vk: int, *, extended: bool = False, hold_s: float = 0.008) -> bool:
    if not send_inputs([key_down(vk, extended=extended)]):
        return False
    time.sleep(max(0.0, hold_s))
    return send_inputs([key_up(vk, extended=extended)])


def press_ctrl_v(*, hold_s: float = 0.012) -> bool:
    """Ctrl+V через SendInput. VK_CONTROL — без KEYEVENTF_EXTENDEDKEY."""
    down = [key_down(VK_CONTROL), key_down(ord("V"))]
    up = [key_up(ord("V")), key_up(VK_CONTROL)]
    if not send_inputs(down):
        return False
    time.sleep(max(0.0, hold_s))
    return send_inputs(up)


def press_enter(*, hold_s: float = 0.008) -> bool:
    return press_key(VK_RETURN, hold_s=hold_s)


def send_unicode_text(text: str, *, chunk: int = 16, delay: float = 0.001) -> bool:
    """Печатает произвольный Unicode-текст через KEYEVENTF_UNICODE.

    ``\\n`` → Enter, ``\\t`` → Tab; суррогатные пары (эмодзи) отправляются
    двумя code units, как того требует Windows.
    """
    if not text:
        return True
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    units = normalized.encode("utf-16-le")
    inputs: list[INPUT] = []
    ok = True
    for index in range(0, len(units), 2):
        unit = units[index] | (units[index + 1] << 8)
        if unit == 0x000A:
            inputs.append(key_down(VK_RETURN))
            inputs.append(key_up(VK_RETURN))
        elif unit == 0x0009:
            inputs.append(key_down(VK_TAB))
            inputs.append(key_up(VK_TAB))
        else:
            inputs.append(keyboard_input(0, unicode=True, scan=unit))
            inputs.append(keyboard_input(0, unicode=True, scan=unit, key_up=True))
        if len(inputs) >= max(2, chunk * 2):
            ok = send_inputs(inputs) and ok
            inputs = []
            time.sleep(max(0.0, delay))
    if inputs:
        ok = send_inputs(inputs) and ok
    return ok
