"""Resolve Anker Turnstile gate via the main UI thread WebView."""

from __future__ import annotations

from typing import Any, Callable

from anker.gate_resolver_worker import spawn_gate_resolve

_GATE_WAIT_SECONDS = 120.0
EventCallback = Callable[[str, dict[str, Any]], None]


def resolve_anker_gate_url(
    gate_url: str,
    *,
    timeout: float = _GATE_WAIT_SECONDS,
    on_event: EventCallback | None = None,
    label: str = "",
) -> str:
    """Run gate page (countdown + Turnstile) in the main QuickPlay window."""
    gate_url = (gate_url or "").strip()
    if not gate_url.startswith("http"):
        raise RuntimeError("Invalid gate URL.")
    if on_event is not None:
        from anker.gate_bridge import request_gate_resolve

        return request_gate_resolve(
            gate_url,
            timeout=timeout,
            on_event=on_event,
            label=label,
        )
    return spawn_gate_resolve(gate_url, timeout=timeout)
