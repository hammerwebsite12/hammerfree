"""AnkerGames client config (copy to config.py — git-ignored)."""

# Base store URL (no trailing slash).
BASE_URL = "https://ankergames.to"

# Legacy host if the primary domain is down again.
FALLBACK_BASE_URLS = ("https://ankergames.net",)

# Optional polite delay between download-link requests (seconds). 0 = disabled.
DOWNLOAD_COOLDOWN_SECONDS = 0
