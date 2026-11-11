"""Глобальная горячая клавиша через низкоуровневый хук WH_KEYBOARD_LL.

Почему так:

* ``WH_KEYBOARD_LL`` — единственный способ поймать клавишу, когда приложение
  не в фокусе, без прав администратора и без сторонних пакетов (``keyboard``
  запрещён). Хук ставится на поток с собственным message loop; коллбэк
  приходит в этот поток и обязан отвечать быстро (Windows снимает медленный хук),
  поэтому коллбэк только обновляет состояние и складывает действия в очередь,
  а пользовательские колбэки выполняет отдельный диспетчер.

Подавление: события, принадлежащие хоткею, глушатся (``return 1``), чтобы
``ctrl+alt+space`` и push-to-talk не просачивались в активное приложение.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import re
import threading
import time
from ctypes import wintypes
from typing import Callable

from ..config import HotkeyConfig

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Win32: хук клавиатуры
# --------------------------------------------------------------------------- #
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_NULL = 0x0000

_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)
_UP_MESSAGES = (WM_KEYUP, WM_SYSKEYUP)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


# --------------------------------------------------------------------------- #
# Спецификация хоткея
# --------------------------------------------------------------------------- #
_MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "alt": "alt",
    "menu": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "meta": "win",
    "super": "win",
    "cmd": "win",
    "leftctrl": "lctrl",
    "left ctrl": "lctrl",
    "leftcontrol": "lctrl",
    "lcrtl": "lctrl",
    "rightctrl": "rctrl",
    "right ctrl": "rctrl",
    "rightcontrol": "rctrl",
    "rcrtl": "rctrl",
    "leftalt": "lalt",
    "left alt": "lalt",
    "rightalt": "ralt",
    "right alt": "ralt",
    "altgr": "ralt",
    "alt gr": "ralt",
    "leftshift": "lshift",
    "left shift": "lshift",
    "rightshift": "rshift",
    "right shift": "rshift",
    "leftwin": "lwin",
    "left win": "lwin",
    "rightwin": "rwin",
    "right win": "rwin",
}

#: Канонические имена модификаторов — принимаются как есть.
_CANONICAL_MODIFIERS = frozenset(
    {"ctrl", "alt", "shift", "win", "lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift", "lwin", "rwin"}
)

_KEY_VK: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "bksp": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "pause": 0x13,
    "break": 0x13,
    "printscreen": 0x2C,
    "print": 0x2C,
    "apps": 0x5D,
    "application": 0x5D,
    "`": 0xC0,
    "backtick": 0xC0,
    "grave": 0xC0,
    "-": 0xBD,
    "minus": 0xBD,
    "dash": 0xBD,
    "=": 0xBB,
    "equal": 0xBB,
    "plus": 0xBB,
    "[": 0xDB,
    "bracketleft": 0xDB,
    "]": 0xDD,
    "bracketright": 0xDD,
    "\\": 0xDC,
    "backslash": 0xDC,
    ";": 0xBA,
    "semicolon": 0xBA,
    "'": 0xDE,
    "quote": 0xDE,
    "apostrophe": 0xDE,
    ",": 0xBC,
    "comma": 0xBC,
    ".": 0xBE,
    "period": 0xBE,
    "dot": 0xBE,
    "/": 0xBF,
    "slash": 0xBF,
}
for _index in range(1, 25):
    _KEY_VK[f"f{_index}"] = 0x70 + _index - 1
for _letter in "abcdefghijklmnopqrstuvwxyz":
    _KEY_VK[_letter] = ord(_letter.upper())
for _digit in "0123456789":
    _KEY_VK[_digit] = 0x30 + int(_digit)

#: Канонические имена модификаторов по виртуальным кодам клавиш.
_MODIFIER_VK: dict[int, tuple[str, ...]] = {
    0xA2: ("lctrl", "ctrl"),
    0xA3: ("rctrl", "ctrl"),
    0x11: ("ctrl",),
    0xA0: ("lshift", "shift"),
    0xA1: ("rshift", "shift"),
    0x10: ("shift",),
    0xA4: ("lalt", "alt"),
    0xA5: ("ralt", "alt"),
    0x12: ("alt",),
    0x5B: ("lwin", "win"),
    0x5C: ("rwin", "win"),
}

#: Обратная карта VK → имена для обычных клавиш (по основной раскладке).
_PRIMARY_NAMES: dict[int, str] = {}
for _name, _vk in _KEY_VK.items():
    # Оставляем первое (основное) имя для каждого кода: "enter" вместо "return".
    _PRIMARY_NAMES.setdefault(_vk, _name)
_PRIMARY_NAMES.setdefault(0x2C, "printscreen")
_PRIMARY_NAMES.setdefault(0x5D, "apps")
for _index in range(1, 25):
    _PRIMARY_NAMES.setdefault(0x70 + _index - 1, f"f{_index}")
for _index in range(10):
    _PRIMARY_NAMES.setdefault(0x60 + _index, f"numpad{_index}")

_VK_TO_NAMES: dict[int, tuple[str, ...]] = dict(_MODIFIER_VK)


def _names_for_vk(vk: int) -> tuple[str, ...]:
    names = _VK_TO_NAMES.get(vk)
    if names is not None:
        return names
    primary = _PRIMARY_NAMES.get(vk)
    if primary is None:
        return ()
    _VK_TO_NAMES[vk] = (primary,)
    return (primary,)


def _normalize_token(token: str) -> str | None:
    """Приводит один токен спецификации к каноническому имени."""
    raw = token.strip().lower()
    if not raw:
        return None
    if raw in _MODIFIER_ALIASES:
        return _MODIFIER_ALIASES[raw]
    if raw in _CANONICAL_MODIFIERS:
        return raw
    vk = _KEY_VK.get(raw)
    if vk is not None:
        # «escape» → «esc», «return» → «enter», «del» → «delete» и т.п.
        return _PRIMARY_NAMES.get(vk, raw)
    # «right-alt», «Right_Alt», «right alt» → «right alt»
    squashed = re.sub(r"[\s_-]+", " ", raw).strip()
    if squashed != raw:
        if squashed in _MODIFIER_ALIASES:
            return _MODIFIER_ALIASES[squashed]
        if squashed in _CANONICAL_MODIFIERS:
            return squashed
        vk = _KEY_VK.get(squashed)
        if vk is not None:
            return _PRIMARY_NAMES.get(vk, squashed)
        unspaced = squashed.replace(" ", "")
        if unspaced in _MODIFIER_ALIASES:
            return _MODIFIER_ALIASES[unspaced]
        if unspaced in _CANONICAL_MODIFIERS:
            return unspaced
        vk = _KEY_VK.get(unspaced)
        if vk is not None:
            return _PRIMARY_NAMES.get(vk, unspaced)
    if raw.startswith("numpad") and raw[6:].isdigit() and 0 <= int(raw[6:]) <= 9:
        return "numpad" + raw[6:]
    if raw.startswith("key ") and raw[4:].strip().isdigit():
        digit = raw[4:].strip()
        return digit if digit in "0123456789" else None
    return None


def parse_hotkey(spec: str) -> frozenset[str]:
    """``'ctrl+alt+space'`` → ``frozenset({'ctrl', 'alt', 'space'})``.

    Поддерживаются синонимы (control/ctrl, esc/escape, right alt/ralt и т.п.),
    пустые и неизвестные части — ``ValueError`` с понятным сообщением.
    """
    if not isinstance(spec, str) or not spec.strip():
        return frozenset()
    parts = re.split(r"\s*\+\s*", spec.strip())
    if any(not part.strip() for part in parts):
        raise ValueError(f"Некорректная горячая клавиша: «{spec.strip()}» (пустая часть)")
    tokens = [part for part in parts if part.strip()]
    if not tokens:
        return frozenset()
    result: set[str] = set()
    for token in tokens:
        name = _normalize_token(token)
        if name is None:
            raise ValueError(f"Неизвестная клавиша в горячей клавише: «{token.strip()}»")
        result.add(name)
    return frozenset(result)


# --------------------------------------------------------------------------- #
# Контроллер
# --------------------------------------------------------------------------- #
class HotkeyController:
    """Реализация :class:`golos.hotkey.base.HotkeyListener`."""

    def __init__(
        self,
        cfg: HotkeyConfig,
        *,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
        state_getter: Callable[[], bool] | None = None,
    ) -> None:
        self._cfg = cfg or HotkeyConfig()
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self._state_getter = state_getter
        self._required = parse_hotkey(self._cfg.hotkey)
        if not self._required:
            raise ValueError("Пустая горячая клавиша: укажите, например, ctrl+alt+space")
        cancel_spec = (self._cfg.cancel_key or "").strip()
        self._cancel_required = parse_hotkey(cancel_spec) if cancel_spec else frozenset()
        self._mode = self._cfg.mode if self._cfg.mode in ("toggle", "push_to_talk") else "toggle"

        self._thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook_handle: int | None = None
        self._hook_proc = HOOKPROC(self._hook_callback)
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._stopping = False
        self._running = False

        self._pressed: set[str] = set()
        self._combo_held = False
        self._toggle_on = False
        self._push_active = False
        self._cancel_down = False
        self._cancel_suppressed = False
        #: Клавиши комбинации, чей keydown мы уже проглотили: их keyup тоже
        #: надо проглотить, иначе приложение получит «залипший» модификатор.
        self._suppressed_down: set[str] = set()
        self._actions: queue.Queue[Callable[[], None] | None] = queue.Queue()

    # ------------------------------------------------------------------ #
    # Публичный интерфейс
    # ------------------------------------------------------------------ #
    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(self._running and thread is not None and thread.is_alive())

    def start(self) -> None:
        """Ставит хук. Идемпотентно; при ошибке — понятное исключение."""
        if self.is_running:
            return
        self._stopping = False
        self._error = None
        self._ready.clear()
        self._reset_state()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="golos-hotkey-actions", daemon=True
        )
        self._dispatch_thread.start()
        self._thread = threading.Thread(
            target=self._run_hook_thread, name="golos-hotkey-hook", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            self.stop()
            raise RuntimeError("Хук клавиатуры не запустился за 2 секунды")
        if self._error is not None:
            error = self._error
            self.stop()
            raise RuntimeError(f"Не удалось установить хук клавиатуры: {error}") from error
        self._running = True

    def stop(self) -> None:
        """Снимает хук и гарантированно завершает потоки."""
        self._stopping = True
        self._actions.put(None)  # разбудить/остановить диспетчер
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread_id = self._thread_id
            if thread_id:
                # WM_QUIT выводит поток из цикла GetMessage.
                if not user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0):
                    for _ in range(5):
                        time.sleep(0.01)
                        if user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0):
                            break
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("Поток хука клавиатуры не завершился за 2 секунды")
        self._thread = None
        self._running = False
        dispatch = self._dispatch_thread
        if dispatch is not None and dispatch.is_alive():
            dispatch.join(timeout=1.0)
        self._dispatch_thread = None
        self._reset_state()

    # ------------------------------------------------------------------ #
    # Потоки
    # ------------------------------------------------------------------ #
    def _run_hook_thread(self) -> None:
        hook: int | None = None
        try:
            self._thread_id = int(kernel32.GetCurrentThreadId())
            # Создаём очередь сообщений потока заранее: PostThreadMessage(stop)
            # должна попасть в уже существующую очередь.
            user32.PostThreadMessageW(self._thread_id, WM_NULL, 0, 0)
            hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hook_proc, kernel32.GetModuleHandleW(None), 0
            )
            if not hook:
                self._error = OSError(
                    f"SetWindowsHookExW вернул NULL (код {ctypes.get_last_error()})"
                )
                self._ready.set()
                return
            self._hook_handle = int(hook)
            self._ready.set()
            message = wintypes.MSG()
            while not self._stopping:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0 or result == -1:  # WM_QUIT или ошибка
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # pragma: no cover - экзотика Windows
            logger.exception("Поток хука клавиатуры упал")
            self._error = exc
            self._ready.set()
        finally:
            if hook:
                user32.UnhookWindowsHookEx(hook)
                self._hook_handle = None
            self._running = False

    def _dispatch_loop(self) -> None:
        while True:
            action = self._actions.get()
            if action is None:
                return
            try:
                action()
            except Exception:  # колбэк не должен убивать диспетчер
                logger.exception("Ошибка в обработчике горячей клавиши")

    def _emit(self, callback: Callable[[], None] | None) -> None:
        if callback is None or self._stopping:
            return
        self._actions.put(callback)

    # ------------------------------------------------------------------ #
    # Хук
    # ------------------------------------------------------------------ #
    def _hook_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code == HC_ACTION:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self._handle_key_event(int(w_param), int(data.vkCode)):
                    return 1
        except Exception:  # коллбэк обязан всегда возвращать значение
            logger.exception("Ошибка в низкоуровневом хуке клавиатуры")
        return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

    def _handle_key_event(self, message: int, vk: int) -> bool:
        """Обновляет состояние и решает, подавлять ли событие."""
        names = _names_for_vk(vk)
        if not names:
            return False
        is_down = message in _DOWN_MESSAGES
        is_up = message in _UP_MESSAGES
        if not (is_down or is_up):
            return False
        if is_down:
            self._pressed.update(names)
        else:
            self._pressed.difference_update(names)

        # --- Отмена (Esc по умолчанию) ---------------------------------- #
        if self._cancel_required and (self._cancel_required & set(names)):
            if is_down:
                if not self._cancel_down:
                    self._cancel_down = True
                    if self._is_active():
                        self._cancel_suppressed = True
                        self._emit(self._on_cancel)
                        return True
                return bool(self._cancel_suppressed)
            # keyup
            suppressed = self._cancel_suppressed
            self._cancel_suppressed = False
            self._cancel_down = False
            return suppressed

        # --- Комбинация хоткея ------------------------------------------ #
        combo_now = self._required <= self._pressed
        was_held = self._combo_held
        if combo_now and not was_held:
            self._combo_held = True
            self._on_combo_pressed()
        elif not combo_now and was_held:
            self._combo_held = False
            self._on_combo_released()

        # Keyup проглоченных клавиш тоже глушим.
        if is_up:
            if self._suppressed_down & set(names):
                self._suppressed_down -= set(names)
                return True
            return False

        # Глушим только клавиши нашей комбинации, пока она «наша»:
        # первое нажатие, автоповтор и повторное нажатие компонентов.
        if (combo_now or self._combo_held) and (set(names) & self._required):
            self._suppressed_down |= set(names) & self._required
            return True
        return False

    def _on_combo_pressed(self) -> None:
        if self._mode == "push_to_talk":
            if not self._push_active:
                self._push_active = True
                self._emit(self._on_start)
            return
        # toggle
        if self._toggle_on:
            self._toggle_on = False
            self._emit(self._on_stop)
        else:
            self._toggle_on = True
            self._emit(self._on_start)

    def _on_combo_released(self) -> None:
        if self._mode == "push_to_talk" and self._push_active:
            self._push_active = False
            self._emit(self._on_stop)

    # ------------------------------------------------------------------ #
    def _is_active(self) -> bool:
        if self._state_getter is not None:
            try:
                return bool(self._state_getter())
            except Exception:
                logger.debug("state_getter упал, использую внутреннее состояние", exc_info=True)
        return self._toggle_on or self._push_active

    def _reset_state(self) -> None:
        self._pressed.clear()
        self._combo_held = False
        self._toggle_on = False
        self._push_active = False
        self._cancel_down = False
        self._cancel_suppressed = False
        self._suppressed_down.clear()


def create_listener(
    cfg: HotkeyConfig,
    *,
    on_start: Callable[[], None],
    on_stop: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    state_getter: Callable[[], bool] | None = None,
) -> HotkeyController:
    """Создаёт слушатель горячей клавиши (хук ставится в ``start()``)."""
    return HotkeyController(
        cfg,
        on_start=on_start,
        on_stop=on_stop,
        on_cancel=on_cancel,
        state_getter=state_getter,
    )
