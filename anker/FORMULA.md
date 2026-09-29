# AnkerGames upstream API

Documents the HTTP flow used by `anker/anker_api.py` and mirrored in
`worker-anker/src/shared/anker_upstream.js`.

**Deployed Workers:** see [../worker-anker/README.md](../worker-anker/README.md).

`game_id` in QuickPlay = Anker **slug** (e.g. `cuphead`), not a numeric id.

---

## Download resolve

| Step | Request | Purpose |
|------|---------|---------|
| 1 | `GET /csrf-token` | Laravel session + CSRF token |
| 2 | `GET /game/{slug}` | Parse numeric `download_id` from HTML |
| 3 | `POST /generate-download-url/{download_id}` | Signed gate URL |
| 4 | `GET {gate_url}` | Parse embedded next hop (see below) |
| 5 | Client GET | Fetch archive from final CDN / dlproxy URL |

### Step 1 — Session + CSRF

```http
GET /csrf-token HTTP/1.1
Host: ankergames.to   (legacy: ankergames.net — same API paths)
Accept: application/json
X-Requested-With: XMLHttpRequest
```

```json
{ "token": "<csrf token>" }
```

Sets cookies: `ankergames_session`, `XSRF-TOKEN`.

### Step 2 — Download id

```http
GET /game/cuphead HTTP/1.1
```

```regex
generateDownloadUrl\(\s*(\d+)\s*\)
```

### Step 3 — Generate download URL

```http
POST /generate-download-url/4766 HTTP/1.1
Content-Type: application/json
X-CSRF-TOKEN: <token from GET /csrf-token — do NOT substitute HTML meta token>
Referer: https://ankergames.to/game/cuphead
Origin: https://ankergames.to

{"g-recaptcha-response":"development-mode"}
```

Use the JSON token from step 1 after loading the game page. The `<meta name="csrf-token">`
on the HTML page is from a rotated session and returns **419** if sent here.

```json
{
  "success": true,
  "download_url": "https://ankergames.to/download/<payload>/<hmac>"
}
```

Worker secret: `ANKER_RECAPTCHA_BYPASS` = `development-mode`.

### Step 4 — Gate page → CDN / dlproxy

The `download_url` is an HTML “treasure box” page. Fetch it and parse:

```regex
downloadPage\(\s*['"]([^'"]+)['"]
```

URL-decode group 1.

**Two outcomes:**

1. **Legacy CDN** — direct archive host, e.g. `stor03.datanodes.to:8443/d/.../Game.zip`
2. **dlproxy tunnel (current)** — e.g. `https://tunnel5.dlproxy.uk/download/<token>`

For **dlproxy**, the URL **is the file** (ZIP/RAR stream). The HTML body may be
huge binary data — do not parse further.

Detection in client: host contains `dlproxy.uk`, `datanodes.to`, or `trashbytes`, or
path ends with `.zip`/`.7z`/`.rar`.

**Downloader note:** dlproxy often returns **403 on HEAD** but **200/206 on GET**
(with or without `Range`). `idm_downloader` probes size via `GET Range: bytes=0-0`.

### Hybrid Worker model

| Step | Where |
|------|-------|
| License + upstream steps 1–4 | **anker-dlresolver** Worker (datacenter OK for Anker HTML) |
| Anker gate HTML (if Worker skipped gate) | Client residential IP |
| Actual file bytes | Client residential IP |

Same HWID pool as PlayZip (`quickplayusr` on GitHub).

---

## Browse / search (client-side only)

| Action | URL |
|--------|-----|
| All games (views) | `GET /games?page={n}` |
| Latest | `GET /recent-updates` |
| Trending | `GET /trending` |
| Genre | `GET /genre/{slug}?page={n}` |
| Search | `GET /search/{url-encoded query}` |

### Listing parse — `/games`, `/trending`, genres

```html
<article listing="{...json...}">
```

JSON fields: `slug`, `title`, `imageurl`, `coverurl`, `genres`, …

### Listing parse — `/recent-updates`

No `listing=` attribute. Parse:

```html
<article aria-label="Game update: Scrap Mechanic">
  <a href="https://ankergames.net/game/scrap-mechanic">...</a>
```

Implemented in `AnkerGamesClient._parse_recent_updates()`.

**Broken:** `GET /games?sort=latest` → HTTP 500 (do not use).

---

## Game details page

Rich metadata is in **JSON-LD** (`VideoGame` schema), not sidebar widgets:

- Genres, screenshots, developer, release date
- Steam store link → app id for trailer (Steam API merge in `anker_game_details.py`)

---

## Differences vs PlayZip (Server 1)

| PlayZip | AnkerGames |
|---------|------------|
| Numeric `game_id` | String **slug** |
| Worker `/sign` + client `POST /api/getGamesDownloadUrl` | Worker `/resolve` or local CSRF flow |
| `SHA256(ts + game_id + SECRET)` digest | Session + `generate-download-url` |
| CDN e.g. trashbytes | Often dlproxy tunnel URLs |
| 125s download cooldown | Optional client cooldown (`DOWNLOAD_COOLDOWN_SECONDS`) |

---

## Worker endpoints (summary)

Full spec: [../worker-anker/README.md](../worker-anker/README.md).

```
POST /resolve  { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }
  → 200 { download_url, snapshot_recorded?: true }
  → 403 { error: "license_required", custom_os?: bool }

POST /license/check  { hwid, device_fp, ts, nonce, sig, token }
  → { licensed: bool, custom_os?: bool }
```

Keep this file updated when ankergames.net changes upstream behavior.
