# Deploy anker-resolver + anker-dlresolver (requires wrangler login or CLOUDFLARE_API_TOKEN).
# Prefer: .\deploy-anker.ps1  (logging + relogin support)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& "$PSScriptRoot\deploy-anker.ps1" @args
