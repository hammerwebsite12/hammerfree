"""Single-instance guard (Windows named mutex).

Running two copies of the app at once corrupts downloads: both instances
call ``resume_pending_downloads()`` against the same
``.playzip_downloads.json`` and write to the same ``.part`` fragments,
racing on the merge/``os.replace`` step. This module makes the second
launch detect the first and bow out gracefully.
"""

from __future__ import annotations

import sys

from native_dialog import show_info

# Global\ namespace so the guard also works across sessions when the app
# is relaunched elevated. Admins hold SeCreateGlobalPrivilege, so this is
# safe for our always-elevated app.
_MUTEX_NAME = "Global\\QuickPlayDownloader_SingleInstance_Mutex"
_WINDOW_TITLE = "QuickPlay"

# Kept for the lifetime of the process; the OS releases it automatically on
# exit. Never garbage-collect this or the mutex disappears.
_mutex_handle = None


def acquire_single_instance() -> bool:
    """Return ``True`` if this is the only instance.

    Returns ``False`` when another instance already owns the mutex. On
    non-Windows platforms (or if the guard cannot be created) this returns
    ``True`` so the app is never blocked from starting.
    """
    global _mutex_handle

    if sys.platform != "win32":
        return True

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.restype = wintypes.HANDLE
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

        handle = create_mutex(None, False, _MUTEX_NAME)
        last_error = ctypes.get_last_error()
    except Exception:
        return True

    error_already_exists = 183
    if not handle:
        return True

    _mutex_handle = handle
    return last_error != error_already_exists


def notify_already_running() -> None:
    """Tell the user the app is already open and focus the existing window."""
    if sys.platform != "win32":
        return

    try:
        _focus_existing_window()
        show_info(
            "QuickPlay is still running.\n\n"
            "Close the other window or switch to it.",
            title="QuickPlay",
        )
    except Exception:
        pass


def _focus_existing_window() -> None:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
