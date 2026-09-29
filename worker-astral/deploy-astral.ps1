# Deploy astral-dlresolver only (Server 3). Does not deploy dl-resolver or anker workers.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path node_modules)) {
    Write-Host "npm install..." -ForegroundColor Cyan
    npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$who = npx wrangler whoami 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $who -match "not authenticated") {
    Write-Host "Wrangler not authenticated. Run: npx wrangler login" -ForegroundColor Red
    exit 1
}

Write-Host "Deploying astral-dlresolver..." -ForegroundColor Cyan
npx wrangler deploy -c wrangler.astral-dlresolver.jsonc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "astral-dlresolver deployed." -ForegroundColor Green
