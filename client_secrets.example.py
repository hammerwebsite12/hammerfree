"""Template for client_secrets.py — copy to client_secrets.py and fill in.

client_secrets.py is git-ignored. These values must match the secrets set on
the Cloudflare Workers (APP_TOKEN, SIGNING_SECRET) and their deployed URLs.

Docs: ANKER.md, worker/README.md, worker-anker/README.md
"""

# Server 1 — PlayZip download-link signer (dl-resolver)
WORKER_URL = "https://dl-resolver.hs2424.workers.dev"

# Server 2 — Anker license gate + download resolver (separate Workers, same tokens)
ANKER_LICENSE_WORKER_URL = "https://anker-resolver.hs2424.workers.dev"
ANKER_DL_WORKER_URL = "https://anker-dlresolver.hs2424.workers.dev"

APP_TOKEN = "set-me-to-the-worker-APP_TOKEN"
SIGNING_SECRET = "set-me-to-the-worker-SIGNING_SECRET"
