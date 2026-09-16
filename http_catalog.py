"""Shared HTTP helpers for store browse/search (stale keep-alive recovery)."""

from __future__ import annotations

import http.client
import time
from typing import Callable, TypeVar

import requests
import urllib3

from idm_downloader import TRANSIENT_ERRORS

CATALOG_CONNECT_TIMEOUT = 12.0
CATALOG_READ_TIMEOUT = 45.0
CATALOG_TIMEOUT = (CATALOG_CONNECT_TIMEOUT, CATALOG_READ_TIMEOUT)
CATALOG_MAX_ATTEMPTS = 3
CATALOG_BACKOFF_SEC = 1.5

T = TypeVar("T")


def is_transient_request_error(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT_ERRORS):
        return True
    if isinstance(exc, requests.RequestException):
        cause = exc.args[0] if exc.args else None
        if isinstance(cause, TRANSIENT_ERRORS):
            return True
    return isinstance(
        exc,
        (
            urllib3.exceptions.ProtocolError,
            urllib3.exceptions.ReadTimeoutError,
            http.client.RemoteDisconnected,
        ),
    )


def renew_session(session: requests.Session, headers: dict[str, str]) -> requests.Session:
    try:
        session.close()
    except OSError:
        pass
    fresh = requests.Session()
    fresh.headers.update(headers)
    return fresh


def retry_transient(
    operation: Callable[[], T],
    *,
    on_retry: Callable[[int, BaseException], None] | None = None,
    max_attempts: int = CATALOG_MAX_ATTEMPTS,
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except BaseException as exc:
            last_exc = exc
            if not is_transient_request_error(exc) or attempt >= max_attempts - 1:
                raise
            if on_retry:
                on_retry(attempt + 1, exc)
            time.sleep(CATALOG_BACKOFF_SEC * (attempt + 1))
    assert last_exc is not None
    raise last_exc
