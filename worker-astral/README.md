# astral-dlresolver (Server 3 scaffold)

Licensed download resolver for **AstralGames** (`https://astralgames.net`).

## Upstream model (differs from Anker)

| | Server 2 (Anker) | Server 3 (Astral) |
|---|------------------|-------------------|
| Stack | Laravel + CSRF + gate/Turnstile | Next.js (RSC payload in HTML) |
| Browse | HTML cards (`uiPostCard`) | Home + `/game/{slug}` (client TBD) |
| Download | `generate-download-url` → gate → CDN | `download_link` / `downloadOptions[].url` in page JSON (often `pearcrypt.lol`) |

Worker fetches `GET /game/{slug}`, regex-extracts embedded `download_link`, returns `{ download_url }`.

## Endpoints

```
GET  /         -> ok
POST /resolve  same auth as anker-dlresolver (APP_TOKEN, SIGNING_SECRET, HWID)
```

License: reuse **anker-resolver** or **dl-resolver** `POST /license/check` on the client until a dedicated `astral-resolver` is added (optional duplicate).

## Secrets (wrangler secret put)

Same as other QuickPlay Workers:

- `APP_TOKEN`
- `SIGNING_SECRET`
- `QUICKPLAY_HWID_PAT`

## Deploy

```powershell
cd worker-astral
npm install
npx wrangler deploy -c wrangler.astral-dlresolver.jsonc
.\copy-secrets.ps1   # or put-hwid-pat.ps1 if PAT was not in .secrets.json
```

**503 `service_unavailable` on download:** `astral-dlresolver` is missing `QUICKPLAY_HWID_PAT` (GitHub access to `quickplayusr`). Set it once (same PAT as `anker-dlresolver`):

```powershell
cd worker-astral
$env:QUICKPLAY_HWID_PAT = 'ghp_...'   # optional: avoid prompt
.\put-hwid-pat.ps1
```

Set client `ASTRAL_DL_WORKER_URL` to the deployed `*.workers.dev` URL (see `client_secrets.example.py`).

## Not included yet

- QuickPlay `store_manager` / UI Server 3 toggle
- `astral/` Python client (browse + `get_download_url`)
- Android gamehub integration
- Follow redirects on `pearcrypt.lol` to final file URL (may need client probe)

See **ASTRAL.md** in repo root.
