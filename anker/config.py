"""AnkerGames client config (copy to config.py — git-ignored)."""

# Base store URL (no trailing slash). Official interim host per Anker Discord (2026-09).
BASE_URL = "https://ankergames.to"

# Tried in order after BASE_URL when the primary is unreachable.
FALLBACK_BASE_URLS = ("https://ankergames.net",)

# Optional polite delay between download-link requests (seconds). 0 = disabled.
DOWNLOAD_COOLDOWN_SECONDS = 0
