"""Dual-store manager — Server 1 and Server 2 catalogs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import requests

from playzip_api import CATEGORIES as PLAYZIP_CATEGORIES
from playzip_api import MIRROR_SITES, USER_AGENT, PlayZipClient, ResolveStatusCallback

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from settings_manager import SettingsManager

STORE_SERVER1 = "server1"
STORE_SERVER2 = "server2"
DEFAULT_STORE = STORE_SERVER1
SUPPORTED_STORES = (STORE_SERVER1, STORE_SERVER2)

STORE_OPTIONS: list[dict[str, str]] = [
    {"id": STORE_SERVER1, "label": "Server 1"},
    {"id": STORE_SERVER2, "label": "Server 2"},
]

EventCallback = Callable[[str, dict[str, Any]], None]
RateLimitCallback = Callable[[int, str], None]
SnapshotCallback = Callable[[], None]

_REACH_TIMEOUT = 12


def normalize_store(value: str | None) -> str:
    store = (value or "").strip().lower()
    return store if store in SUPPORTED_STORES else DEFAULT_STORE


def store_label(store_id: str) -> str:
    for opt in STORE_OPTIONS:
        if opt["id"] == store_id:
            return opt["label"]
    return store_id


class StoreManager:
    """Selects browse/search/download client based on active store setting."""

    def __init__(
        self,
        settings: SettingsManager,
        logger: DownloadLogger | None = None,
        on_rate_limit: RateLimitCallback | None = None,
        on_snapshot_recorded: SnapshotCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self._on_rate_limit = on_rate_limit
        self._on_resolve_status: ResolveStatusCallback | None = None
        self._on_snapshot_recorded = on_snapshot_recorded
        self._on_event = on_event
        self._active = normalize_store(settings.store)
        self._cached_playzip: PlayZipClient | None = None
        self._cached_anker: Any | None = None
        self._last_switch_reason = ""
        self._apply_license_urls()
        self._apply_log_sanitize_mode()

    def set_rate_limit_callback(self, callback: RateLimitCallback | None) -> None:
        self._on_rate_limit = callback
        self._cached_playzip = None
        self._cached_anker = None

    def set_resolve_status_callback(
        self,
        callback: ResolveStatusCallback | None,
    ) -> None:
        self._on_resolve_status = callback
        self._cached_playzip = None
        self._cached_anker = None

    @property
    def active_store(self) -> str:
        return self._active

    @property
    def is_anker(self) -> bool:
        return self._active == STORE_SERVER2

    @property
    def last_switch_reason(self) -> str:
        return self._last_switch_reason

    def get_categories(self) -> list[dict[str, str]]:
        if self.is_anker:
            from anker.anker_api import CATEGORIES

            return list(CATEGORIES)
        return list(PLAYZIP_CATEGORIES)

    def get_client(self) -> PlayZipClient:
        if self.is_anker:
            return self._get_anker_client()
        return self._get_playzip_client()

    def get_client_for(self, store_id: str) -> PlayZipClient:
        """Return browse client for a specific store id (server1/server2)."""
        if normalize_store(store_id) == STORE_SERVER2:
            return self._get_anker_client()
        return self._get_playzip_client()

    def create_details_service(self, logger: DownloadLogger):
        if self.is_anker:
            from anker.anker_game_details import AnkerGameDetailsService

            return AnkerGameDetailsService(self.get_client(), logger)
        from game_details import GameDetailsService

        return GameDetailsService(self.get_client(), logger)

    def set_store(
        self,
        store_id: str,
        *,
        persist: bool = True,
        auto: bool = False,
        reason: str = "",
    ) -> bool:
        store = normalize_store(store_id)
        if store == self._active:
            return False

        self._active = store
        self._last_switch_reason = reason
        if persist:
            self.settings.set_store(store)
        self._apply_license_urls()
        self._apply_log_sanitize_mode()

        label = store_label(store)
        msg = (
            f"Auto-switched to {label} ({reason})"
            if auto and reason
            else f"Store set to {label}"
        )
        if self.logger:
            self.logger.info(msg)
        if self._on_event:
            payload = {
                "store": store,
                "store_label": label,
                "auto": auto,
                "reason": reason,
                **self.settings.to_dict(),
            }
            self._on_event("store_changed", payload)
            self._on_event("settings_updated", payload)
        return True

    def ensure_startup_store(self) -> bool:
        """If Server 1 is saved but unreachable on launch, auto-switch to Server 2."""
        if self._active != STORE_SERVER1:
            return False
        if self.check_server1_reachable():
            return False
        if not self.check_server2_reachable():
            if self.logger:
                self.logger.warn(
                    "Primary store unreachable and alternate store also unavailable."
                )
            return False
        return self.set_store(
            STORE_SERVER2,
            persist=True,
            auto=True,
            reason="server1_unreachable",
        )

    def check_server1_reachable(self) -> bool:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        for mirror in MIRROR_SITES:
            try:
                resp = session.get(mirror, timeout=_REACH_TIMEOUT)
                if resp.status_code < 500:
                    return True
            except requests.RequestException:
                continue
        return False

    def check_server2_reachable(self) -> bool:
        try:
            from anker.config import BASE_URL
        except ImportError:
            from anker.config.example import BASE_URL  # type: ignore[import-not-found]

        base = str(BASE_URL).rstrip("/")
        try:
            resp = requests.get(base, timeout=_REACH_TIMEOUT)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def browse(
        self,
        page: int = 1,
        sort: str = "views",
        category: str = "all",
    ) -> tuple[list, dict[str, Any]]:
        meta: dict[str, Any] = {
            "store": self._active,
            "store_label": store_label(self._active),
            "store_switched": False,
        }
        try:
            games = self.get_client().browse(page=page, sort=sort, category=category)
            return games, meta
        except RuntimeError as exc:
            if self._active != STORE_SERVER1:
                raise
            if not self.check_server2_reachable():
                raise
            switched = self.set_store(
                STORE_SERVER2,
                persist=True,
                auto=True,
                reason="browse_unreachable",
            )
            if not switched:
                raise
            meta["store"] = self._active
            meta["store_label"] = store_label(self._active)
            meta["store_switched"] = True
            meta["store_switch_reason"] = "browse_unreachable"
            games = self.get_client().browse(page=page, sort=sort, category=category)
            return games, meta

    def search(self, query: str) -> tuple[list, dict[str, Any]]:
        meta: dict[str, Any] = {
            "store": self._active,
            "store_label": store_label(self._active),
            "store_switched": False,
        }
        q = (query or "").strip()
        if not q:
            return [], meta

        games = self.get_client().search(q)
        if games or self._active != STORE_SERVER1:
            return games, meta

        if not self.check_server2_reachable():
            return games, meta

        switched = self.set_store(
            STORE_SERVER2,
            persist=True,
            auto=True,
            reason="search_no_results",
        )
        if not switched:
            return games, meta

        alt_games = self.get_client().search(q)
        if not alt_games:
            return games, meta

        meta["store"] = self._active
        meta["store_label"] = store_label(self._active)
        meta["store_switched"] = True
        meta["store_switch_reason"] = "search_no_results"
        return alt_games, meta

    def _get_playzip_client(self) -> PlayZipClient:
        if self._cached_playzip is None:
            self._cached_playzip = PlayZipClient(
                logger=self.logger,
                on_rate_limit=self._on_rate_limit,
                on_resolve_status=self._on_resolve_status,
                hardware_snapshot_submitted=self.settings.hardware_snapshot_submitted,
                on_snapshot_recorded=self._on_snapshot_recorded,
            )
        return self._cached_playzip

    def _get_anker_client(self) -> Any:
        if self._cached_anker is None:
            from anker.anker_api import AnkerGamesClient

            self._cached_anker = AnkerGamesClient(
                logger=self.logger,
                on_rate_limit=self._on_rate_limit,
                on_resolve_status=self._on_resolve_status,
                hardware_snapshot_submitted=self.settings.hardware_snapshot_submitted,
                on_snapshot_recorded=self._on_snapshot_recorded,
                on_event=self._on_event,
            )
        return self._cached_anker

    def _apply_license_urls(self) -> None:
        import license_manager

        token = ""
        secret = ""
        worker_url = ""
        try:
            import client_secrets as cs

            token = (getattr(cs, "APP_TOKEN", None) or "").strip()
            secret = (getattr(cs, "SIGNING_SECRET", None) or "").strip()
            if self.is_anker:
                worker_url = (getattr(cs, "ANKER_LICENSE_WORKER_URL", None) or "").strip()
            else:
                worker_url = (getattr(cs, "WORKER_URL", None) or "").strip()
        except ImportError:
            pass

        license_manager.WORKER_URL = worker_url
        license_manager.APP_TOKEN = token
        license_manager.SIGNING_SECRET = secret

    def _apply_log_sanitize_mode(self) -> None:
        from log_sanitize import set_anker_sanitize

        set_anker_sanitize(self.is_anker)
