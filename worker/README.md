# dl-resolver — download-link signer (Cloudflare Worker)

Keeps the upstream download secret **off the client**. The EXE sends a signed
request; this Worker returns a short-lived digest. The client (residential IP)
performs the actual upstream call, because the upstream blocks datacenter IPs.

**Keep this repo private / do not publish** — the code documents the recipe
shape. The actual secret *values* live in Cloudflare (not in this code).

## Endpoints

```
POST /license/check
  body: { hwid, device_fp, ts, nonce, sig, token }
    sig = HMAC_SHA256(SIGNING_SECRET, "{ts}.{nonce}.{hwid}.{device_fp}")  (hex)
  -> 200 { licensed: bool, custom_os?: bool }
```

Used **once on first app startup** (no download signing, no HW snapshot).

```
POST /sign
  body: { game_id, hwid, device_fp, ts, nonce, sig, token, hw_snapshot? }
    token = APP_TOKEN
    sig   = HMAC_SHA256(SIGNING_SECRET, "{ts}.{nonce}.{game_id}.{hwid}.{device_fp}")  (hex)
  -> 200 { timestamp, digest, cookie, snapshot_recorded?: true }
  -> 403 { error: "license_required", custom_os?: bool }
  -> 401 unauthorized | expired | bad_signature
  -> 409 replay
  -> 429 rate_limited
```

On the **first licensed** `/sign`, if `hw_snapshot` is present and the `.user` file
has no `Hardware-Snapshot-Recorded:` marker yet, the Worker appends the snapshot to
`quickplayusr` (one-time) and may post a discreet Discord alert.

`QUICKPLAY_HWID_PAT` must have **read + write** access to `hammerwebsite12/quickplayusr`.

`GET /` returns `ok` (health check).

## Deploy

```bash
npm install
npx wrangler deploy
```

Current URL: `https://dl-resolver.hs2424.workers.dev`
Account: hanahsong2424@gmail.com

## Secrets (set once / to rotate)

```bash
# interactive
wrangler secret put APP_TOKEN
wrangler secret put SIGNING_SECRET
wrangler secret put PLAYZIP_SECRET_KEY
wrangler secret put PLAYZIP_AUTH_COOKIE
wrangler secret put QUICKPLAY_HWID_PAT
wrangler secret put DISCORD_WEBHOOK_URL

# or bulk (do NOT commit the file; delete after)
wrangler secret bulk .secrets.json
```

After rotating `APP_TOKEN` / `SIGNING_SECRET`, update the client's
`client_secrets.py` (in the playzip app repo) to match, and rebuild the EXE.

## Tunables (wrangler.jsonc `vars`)

- `SIGNATURE_WINDOW_SECONDS` (default 120) — allowed client/server clock drift
- `RATE_LIMIT_PER_MINUTE` (default 20) — per-IP `/sign` calls per minute

## Bindings

- `PZ_KV` — KV namespace for nonce replay dedupe + per-IP rate limiting
