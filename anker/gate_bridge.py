"""Bridge Anker gate resolve from download thread → main UI thread."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

EventCallback = Callable[[str, dict[str, Any]], None]

_lock = threading.Lock()
_pending: _GatePending | None = None


@dataclass
class _GatePending:
    gate_url: str
    label: str = ""
    on_event: EventCallback | None = None
    done: threading.Event = field(default_factory=threading.Event)
    result_url: str = ""
    error: str = ""


def report_gate_progress(countdown: int, message: str, phase: str) -> None:
    """Mirror hidden gate page status onto the active download task."""
    with _lock:
        pending = _pending
    if pending is None or pending.on_event is None:
        return
    pending.on_event(
        "gate_progress",
        {
            "label": pending.label,
            "countdown": max(0, int(countdown)),
            "status_message": (message or "").strip() or "Preparing download verification...",
            "phase": (phase or "waiting").strip(),
        },
    )


def abort_pending_gate(error: str = "Download verification cancelled.") -> None:
    """Wake any blocked gate wait and close the verification window."""
    with _lock:
        global _pending
        pending = _pending
        _pending = None
    if pending is None:
        return
    pending.error = (error or "").strip() or "Download verification cancelled."
    pending.done.set()
    try:
        from anker.gate_resolver_worker import close_active_gate_window

        close_active_gate_window()
    except Exception:
        pass


def request_gate_resolve(
    gate_url: str,
    *,
    timeout: float,
    on_event: EventCallback | None,
    label: str = "",
) -> str:
    """Block until the gate page finishes (hidden subprocess or UI overlay)."""
    gate_url = (gate_url or "").strip()
    if not gate_url.startswith("http"):
        raise RuntimeError("Invalid gate URL.")

    abort_pending_gate("Download verification restarted.")

    pending = _GatePending(gate_url=gate_url, label=(label or "").strip(), on_event=on_event)
    with _lock:
        global _pending
        _pending = pending

    try:
        if on_event:
            if label:
                on_event(
                    "gate_progress",
                    {
                        "label": label,
                        "countdown": 0,
                        "status_message": "Preparing download verification...",
                        "phase": "waiting",
                    },
                )
            on_event(
                "gate_resolve",
                {"gate_url": gate_url},
            )
        else:
            raise RuntimeError("Gate UI bridge unavailable.")

        if not pending.done.wait(timeout):
            abort_pending_gate("Timed out resolving download link. Subukan ulit.")
            raise RuntimeError("Timed out resolving download link. Subukan ulit.")

        if pending.error:
            raise RuntimeError(pending.error)
        if not pending.result_url.startswith("http"):
            raise RuntimeError("Could not resolve download link. Subukan ulit.")
        return pending.result_url
    finally:
        with _lock:
            if _pending is pending:
                _pending = None


def complete_gate_resolve(
    gate_url: str,
    *,
    result_url: str = "",
    error: str = "",
) -> bool:
    with _lock:
        global _pending
        if _pending is None or _pending.gate_url != gate_url:
            return False
        pending = _pending
        _pending = None
    pending.result_url = (result_url or "").strip()
    pending.error = (error or "").strip()
    pending.done.set()
    try:
        from anker.gate_resolver_worker import close_active_gate_window

        close_active_gate_window()
    except Exception:
        pass
    return True


def cancel_gate_resolve(gate_url: str) -> None:
    with _lock:
        if _pending is None or _pending.gate_url != gate_url:
            return
    abort_pending_gate("Timed out resolving download link. Subukan ulit.")
