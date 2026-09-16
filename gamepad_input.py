"""Windows XInput reader — WebView2's Gamepad API is unreliable in pywebview."""

from __future__ import annotations

import sys
import threading
import time
from ctypes import Structure, WinDLL, byref, wintypes

if sys.platform != "win32":
    _XINPUT = None
else:
    _XINPUT = None
    for _dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            _XINPUT = WinDLL(_dll)
            break
        except OSError:
            continue

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167

DPAD_UP = 0x0001
DPAD_DOWN = 0x0002
DPAD_LEFT = 0x0004
DPAD_RIGHT = 0x0008
BTN_A = 0x1000
BTN_B = 0x2000
BTN_LB = 0x0100
BTN_RB = 0x0200

STICK_DEAD = 0.42

_EMPTY_STATE: dict = {"connected": False, "dir": None, "edge": {}, "active": False}


class XINPUT_GAMEPAD(Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", wintypes.BYTE),
        ("bRightTrigger", wintypes.BYTE),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]


class XINPUT_STATE(Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


_lock = threading.Lock()
_latest: dict = dict(_EMPTY_STATE)
_prev_buttons: dict[int, int] = {}
_running = False


def _norm_stick(value: int) -> float:
    if value >= 0:
        return min(1.0, value / 32767.0)
    return max(-1.0, value / 32768.0)


def _read_pad(user_index: int) -> XINPUT_STATE | None:
    if _XINPUT is None:
        return None
    state = XINPUT_STATE()
    result = _XINPUT.XInputGetState(user_index, byref(state))
    if result == ERROR_DEVICE_NOT_CONNECTED:
        return None
    if result != ERROR_SUCCESS:
        return None
    return state


def _stick_dir(x: float, y: float) -> str | None:
    if abs(x) < STICK_DEAD and abs(y) < STICK_DEAD:
        return None
    if abs(x) > abs(y):
        return "left" if x < 0 else "right"
    return "up" if y < 0 else "down"


def _build_payload(user_index: int, state: XINPUT_STATE) -> dict:
    gp = state.Gamepad
    buttons = int(gp.wButtons)
    prev = _prev_buttons.get(user_index, 0)
    _prev_buttons[user_index] = buttons

    def edge(mask: int) -> bool:
        return bool(buttons & mask) and not bool(prev & mask)

    stick_x = _norm_stick(int(gp.sThumbLX))
    stick_y = _norm_stick(int(gp.sThumbLY))

    dir_name = None
    if buttons & DPAD_UP:
        dir_name = "up"
    elif buttons & DPAD_DOWN:
        dir_name = "down"
    elif buttons & DPAD_LEFT:
        dir_name = "left"
    elif buttons & DPAD_RIGHT:
        dir_name = "right"
    else:
        dir_name = _stick_dir(stick_x, stick_y)

    edges = {
        "a": edge(BTN_A),
        "b": edge(BTN_B),
        "lb": edge(BTN_LB),
        "rb": edge(BTN_RB),
    }
    active = bool(dir_name) or any(edges.values())

    return {
        "index": user_index,
        "dir": dir_name,
        "edge": edges,
        "connected": True,
        "active": active,
    }


def get_latest_state() -> dict:
    with _lock:
        return dict(_latest)


def is_gamepad_connected() -> bool:
    with _lock:
        return bool(_latest.get("connected"))


def _poll_loop() -> None:
    while _running:
        found = False
        for user_index in range(4):
            state = _read_pad(user_index)
            if state is None:
                _prev_buttons.pop(user_index, None)
                continue
            payload = _build_payload(user_index, state)
            with _lock:
                _latest = payload
            found = True
            break

        if not found:
            with _lock:
                _latest = dict(_EMPTY_STATE)

        time.sleep(0.03)


def start_gamepad_bridge() -> None:
    """Start background XInput polling (always on while the app runs)."""
    global _running
    if sys.platform != "win32" or _XINPUT is None or _running:
        return
    _running = True
    thread = threading.Thread(
        target=_poll_loop,
        name="quickplay-xinput",
        daemon=True,
    )
    thread.start()


def stop_gamepad_bridge() -> None:
    global _running
    _running = False
