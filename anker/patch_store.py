"""Monkey-patch PlayZip store imports to use AnkerGames (call before backend init)."""

from __future__ import annotations


def apply() -> None:
    import game_details
    import license_manager
    import playzip_api
    from anker import anker_api
    from anker import anker_game_details

    try:
        import client_secrets as cs
    except ImportError:
        cs = None

    if cs is not None:
        anker_license_url = (getattr(cs, "ANKER_LICENSE_WORKER_URL", None) or "").strip()
        anker_dl_url = (getattr(cs, "ANKER_DL_WORKER_URL", None) or "").strip()
        app_token = (getattr(cs, "APP_TOKEN", None) or "").strip()
        signing_secret = (getattr(cs, "SIGNING_SECRET", None) or "").strip()
    else:
        anker_license_url = anker_dl_url = app_token = signing_secret = ""

    playzip_api.PlayZipClient = anker_api.AnkerGamesClient
    playzip_api.CATEGORIES = anker_api.CATEGORIES
    playzip_api.MIRROR_SITES = (anker_api.BASE_URL,)
    playzip_api.WORKER_URL = anker_license_url or ""
    playzip_api.APP_TOKEN = app_token
    playzip_api.SIGNING_SECRET = signing_secret

    license_manager.WORKER_URL = anker_license_url or ""
    license_manager.APP_TOKEN = app_token
    license_manager.SIGNING_SECRET = signing_secret

    anker_api.ANKER_DL_WORKER_URL = anker_dl_url or ""
    anker_api.APP_TOKEN = app_token
    anker_api.SIGNING_SECRET = signing_secret

    game_details.GameDetailsService = anker_game_details.AnkerGameDetailsService
