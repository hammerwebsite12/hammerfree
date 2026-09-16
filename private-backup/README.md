# Private backup (playzipdl only)

Machine-local copies for disaster recovery. **Do not publish this folder to a public repo.**

| File | Source | Restore |
|------|--------|---------|
| `settings.json` | `D:\My Drive\Workspace\plazipanker\settings.json` | Copy to app root next to `QuickPlay.exe` / `main.py` |
| `library.json` | Desktop dev `dist\library.json` | Copy to app root (edit `install_dir` / `exe_path` if paths changed) |
| `worker-secrets.json` | Built from `client_secrets.py` + Anker defaults | `wrangler secret bulk worker-secrets.json` (fill `QUICKPLAY_HWID_PAT` first — not stored on disk locally; only on Cloudflare) |
| `client_secrets.py` | Same as repo root | Copy to app root for dev builds |

`QUICKPLAY_HWID_PAT` was **not** found under `plazipanker` or common paths. After device loss, rotate or recreate a GitHub PAT with read/write on `hammerwebsite12/quickplayusr`, then update Workers via Wrangler.

Last synced from workspace: 2026-09-16.
