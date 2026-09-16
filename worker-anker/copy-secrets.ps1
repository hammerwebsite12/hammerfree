# Copy secrets from playzip dl-resolver bundle to anker workers.
# Cloudflare does NOT allow reading secret values back — you must have the
# original .secrets.json used when dl-resolver was first deployed.
param(
    [string]$SecretsFile = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$candidates = @(
    $SecretsFile,
    (Join-Path $PSScriptRoot "..\\worker\\.secrets.json"),
    (Join-Path $PSScriptRoot ".secrets.json")
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $candidates -or $candidates.Count -eq 0) {
    Write-Host "No .secrets.json found." -ForegroundColor Red
    Write-Host "Copy your dl-resolver secrets file to worker\.secrets.json (see .secrets.example.json), then rerun." -ForegroundColor Yellow
    exit 1
}

$file = $candidates[0]
Write-Host "Using secrets file: $file" -ForegroundColor Cyan

$json = Get-Content $file -Raw | ConvertFrom-Json
$ankerDl = @{}
foreach ($key in @("APP_TOKEN", "SIGNING_SECRET", "QUICKPLAY_HWID_PAT", "DISCORD_WEBHOOK_URL")) {
    if ($json.PSObject.Properties.Name -contains $key -and "$($json.$key)".Trim()) {
        $ankerDl[$key] = "$($json.$key)".Trim()
    }
}
$ankerDl["ANKER_RECAPTCHA_BYPASS"] = if ($json.ANKER_RECAPTCHA_BYPASS) { "$($json.ANKER_RECAPTCHA_BYPASS)" } else { "development-mode" }

$resolverOnly = @{}
foreach ($key in @("APP_TOKEN", "SIGNING_SECRET", "QUICKPLAY_HWID_PAT", "DISCORD_WEBHOOK_URL")) {
    if ($ankerDl.ContainsKey($key)) { $resolverOnly[$key] = $ankerDl[$key] }
}

$resPath = Join-Path $env:TEMP "anker-resolver-secrets.json"
$dlPath = Join-Path $env:TEMP "anker-dlresolver-secrets.json"
($resolverOnly | ConvertTo-Json) | Set-Content $resPath -Encoding utf8
($ankerDl | ConvertTo-Json) | Set-Content $dlPath -Encoding utf8

Write-Host "Uploading to anker-resolver..." -ForegroundColor Cyan
npx wrangler secret bulk $resPath -c wrangler.anker-resolver.jsonc
Write-Host "Uploading to anker-dlresolver..." -ForegroundColor Cyan
npx wrangler secret bulk $dlPath -c wrangler.anker-dlresolver.jsonc

Remove-Item $resPath, $dlPath -Force -ErrorAction SilentlyContinue
Write-Host "Secrets copied. PlayZip dl-resolver was NOT modified." -ForegroundColor Green
