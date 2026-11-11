"""Тесты горячей клавиши: парсер спецификации, цикл жизни хука, реальные нажатия.

Тесты с реальными нажатиями вводят их через SendInput в собственное окно Qt
(песочница/сессия могут не дать вывести окно на передний план — тогда skip).
Хоткей при этом проверяется и «сквозным» образом: активное окно не должно
получать клавиши комбинации.
"""

from __future__ import annotations

import os
import time

import pytest

from golos.config import HotkeyConfig
from golos.hotkey import create_listener, parse_hotkey
from golos.inject import _win32

VK_F13 = 0x7C
VK_SPACE = 0x20


# --------------------------------------------------------------------------- #
# Парсер спецификации
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ctrl+alt+space", {"ctrl", "alt", "space"}),
        ("Control + Alt + Space", {"ctrl", "alt", "space"}),
        ("CTRL+SHIFT+D", {"ctrl", "shift", "d"}),
        ("f9", {"f9"}),
        ("F24", {"f24"}),
        ("right alt", {"ralt"}),
        ("right-alt", {"ralt"}),
        ("altgr", {"ralt"}),
        ("right ctrl+space", {"rctrl", "space"}),
        ("left shift+f1", {"lshift", "f1"}),
        ("capslock", {"capslock"}),
        ("esc", {"esc"}),
        ("escape", {"esc"}),
        ("enter", {"enter"}),
        ("return", {"enter"}),
        ("tab", {"tab"}),
        ("backspace", {"backspace"}),
        ("del", {"delete"}),
        ("delete", {"delete"}),
        ("pageup", {"pageup"}),
        ("pgdn", {"pagedown"}),
        ("up", {"up"}),
        ("down", {"down"}),
        ("left", {"left"}),
        ("right", {"right"}),
        ("win", {"win"}),
        ("ctrl+alt+f12", {"ctrl", "alt", "f12"}),
        ("ctrl+shift+1", {"ctrl", "shift", "1"}),
        ("ctrl+/", {"ctrl", "/"}),
        ("ctrl+-", {"ctrl", "-"}),
    ],
)
def test_parse_hotkey_synonyms(spec: str, expected: set[str]) -> None:
    assert parse_hotkey(spec) == frozenset(expected)


@pytest.mark.parametrize("spec", ["", "   ", None])
def test_parse_hotkey_empty(spec) -> None:
    assert parse_hotkey(spec) == frozenset()  # type: ignore[arg-type]


def test_parse_hotkey_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Неизвестная клавиша"):
        parse_hotkey("ctrl+чебурашка")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl++alt")


def test_create_listener_rejects_empty_hotkey() -> None:
    with pytest.raises(ValueError):
        create_listener(HotkeyConfig(hotkey=""), on_start=lambda: None, on_stop=lambda: None)


# --------------------------------------------------------------------------- #
# Цикл жизни без нажатий
# --------------------------------------------------------------------------- #
def test_start_stop_lifecycle() -> None:
    calls: list[str] = []
    listener = create_listener(
        HotkeyConfig(hotkey="ctrl+alt+f13"),
        on_start=lambda: calls.append("start"),
        on_stop=lambda: calls.append("stop"),
    )
    assert listener.is_running is False
    listener.start()
    try:
        assert listener.is_running is True
        listener.start()  # идемпотентно
        assert listener.is_running is True
    finally:
        start = time.perf_counter()
        listener.stop()
        elapsed = time.perf_counter() - start
    assert listener.is_running is False
    assert elapsed < 2.0, f"stop() занял {elapsed:.2f} с"
    listener.stop()  # повторный stop — no-op
    assert calls == []


def test_stop_without_start_is_noop() -> None:
    listener = create_listener(HotkeyConfig(), on_start=lambda: None, on_stop=lambda: None)
    listener.stop()
    assert listener.is_running is False


# --------------------------------------------------------------------------- #
# Реальные синтетические нажатия
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal"}:
        pytest.skip("QT_QPA_PLATFORM=offscreen: реальное окно недоступно")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip(f"Qt-платформа {app.platformName()!r}: нужен реальный Windows-дисплей")
    return app


def pump(app, seconds: float = 0.15) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def focused_window(qt_app):
    from PySide6.QtWidgets import QLineEdit

    window = QLineEdit()
    window.resize(420, 60)
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()
    pump(qt_app, 0.35)
    if int(_win32.get_foreground_window()) != int(window.winId()):
        window.close()
        qt_app.processEvents()
        pytest.skip("не удалось вывести тестовое окно на передний план")
    yield window
    window.close()
    qt_app.processEvents()


def _send_keys(vk_codes: list[int], *, down: bool) -> None:
    inputs = [_win32.keyboard_input(vk, key_up=not down) for vk in vk_codes]
    if not down:
        inputs = list(reversed(inputs))
    assert _win32.send_inputs(inputs)


def tap_combo(vk_codes: list[int], hold_s: float = 0.05) -> None:
    _send_keys(vk_codes, down=True)
    time.sleep(hold_s)
    _send_keys(vk_codes, down=False)


def wait_for(predicate, qt_app, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


COMBO = [_win32.VK_CONTROL, _win32.VK_MENU, VK_F13]


def _make_listener(mode: str, events: list[str], state: dict[str, bool] | None = None):
    state = state if state is not None else {"active": False}

    def on_start() -> None:
        state["active"] = True
        events.append("start")

    def on_stop() -> None:
        state["active"] = False
        events.append("stop")

    def on_cancel() -> None:
        events.append("cancel")

    listener = create_listener(
        HotkeyConfig(hotkey="ctrl+alt+f13", mode=mode, cancel_key="esc"),
        on_start=on_start,
        on_stop=on_stop,
        on_cancel=on_cancel,
        state_getter=lambda: state["active"],
    )
    return listener, state


def test_toggle_mode_real_presses(qt_app, focused_window) -> None:
    events: list[str] = []
    listener, _ = _make_listener("toggle", events)
    listener.start()
    try:
        tap_combo(COMBO)
        assert wait_for(lambda: events.count("start") == 1, qt_app), events
        assert events == ["start"]
        tap_combo(COMBO)
        assert wait_for(lambda: events.count("stop") == 1, qt_app), events
        assert events == ["start", "stop"]
    finally:
        listener.stop()


def test_push_to_talk_real_presses(qt_app, focused_window) -> None:
    events: list[str] = []
    listener, _ = _make_listener("push_to_talk", events)
    listener.start()
    try:
        _send_keys(COMBO, down=True)
        assert wait_for(lambda: events == ["start"], qt_app), events
        time.sleep(0.15)
        _send_keys(COMBO, down=False)
        assert wait_for(lambda: events == ["start", "stop"], qt_app), events
    finally:
        listener.stop()


def test_cancel_only_while_active(qt_app, focused_window) -> None:
    events: list[str] = []
    listener, state = _make_listener("toggle", events)
    listener.start()
    try:
        state["active"] = True
        _win32.press_key(_win32.VK_ESCAPE)
        assert wait_for(lambda: "cancel" in events, qt_app), events
        count = events.count("cancel")
        state["active"] = False
        _win32.press_key(_win32.VK_ESCAPE)
        pump(qt_app, 0.4)
        assert events.count("cancel") == count, events
    finally:
        listener.stop()


def test_autorepeat_does_not_spam(qt_app, focused_window) -> None:
    events: list[str] = []
    listener, _ = _make_listener("toggle", events)
    listener.start()
    try:
        _send_keys(COMBO, down=True)
        assert wait_for(lambda: events == ["start"], qt_app), events
        # Имитируем автоповтор: повторные keydown той же клавиши.
        for _ in range(3):
            _send_keys([VK_F13], down=True)
            time.sleep(0.03)
        pump(qt_app, 0.3)
        assert events == ["start"], events
        _send_keys(COMBO, down=False)
    finally:
        listener.stop()


def test_hotkey_is_suppressed(qt_app, focused_window) -> None:
    """Комбинация не должна доходить до активного окна (пробел не печатается)."""
    events: list[str] = []
    state: dict[str, bool] = {"active": False}

    def on_start() -> None:
        state["active"] = True
        events.append("start")

    def on_stop() -> None:
        state["active"] = False
        events.append("stop")

    listener = create_listener(
        HotkeyConfig(hotkey="ctrl+alt+space", mode="toggle"),
        on_start=on_start,
        on_stop=on_stop,
        state_getter=lambda: state["active"],
    )
    listener.start()
    try:
        space_combo = [_win32.VK_CONTROL, _win32.VK_MENU, VK_SPACE]
        tap_combo(space_combo)
        assert wait_for(lambda: events == ["start"], qt_app), events
        tap_combo(space_combo)
        assert wait_for(lambda: events == ["start", "stop"], qt_app), events
        pump(qt_app, 0.2)
        # Ctrl+Alt+Space не должен вставить пробел в поле ввода.
        assert focused_window.text() == ""
    finally:
        listener.stop()
