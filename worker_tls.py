"""Guard Worker API HTTP calls — only trusted Cloudflare Worker hostnames."""

from __future__ import annotations

import functools
from typing import Any, Callable
from urllib.parse import urlparse

# QuickPlay production Workers account subdomain. Other *.workers.dev hosts are rejected.
_TRUSTED_WORKER_SUFFIXES = (".hs2424.workers.dev",)

_installed = False


def _trusted_worker_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    return any(host.endswith(suffix) for suffix in _TRUSTED_WORKER_SUFFIXES)


def _guard_worker_url(url: str) -> None:
    """Block MITM redirects to attacker-controlled Worker endpoints."""
    try:
        host = urlparse(str(url)).hostname or ""
    except Exception:
        host = ""
    if not host.endswith(".workers.dev"):
        return
    if not _trusted_worker_host(host):
        raise ConnectionError(f"Untrusted Worker host: {host}")


def _wrap_requests_call(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        url = kwargs.get("url")
        if url is None and args:
            url = args[1] if len(args) > 1 else args[0]
        if url is not None:
            _guard_worker_url(url)
        return fn(*args, **kwargs)

    return wrapper


def install_worker_tls_guard() -> None:
    """Patch requests so only our Worker hostnames are used for *.workers.dev calls."""
    global _installed
    if _installed:
        return
    import requests

    requests.request = _wrap_requests_call(requests.request)
    for verb in ("get", "post", "head", "put", "delete", "patch", "options"):
        if hasattr(requests, verb):
            setattr(requests, verb, _wrap_requests_call(getattr(requests, verb)))
    _installed = True
