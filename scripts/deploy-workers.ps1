# Redeploy all QuickPlay signer workers (hanahsong2424 account).
# Usage: .\scripts\deploy-workers.ps1
param([switch]$SkipLogin)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $repoRoot
$workerDir = Join-Path $repoRoot "worker"
$ankerDir = Join-Path $repoRoot "worker-anker"

Write-Host "`n=== QuickPlay Worker Redeploy ===`n" -ForegroundColor Cyan

if (-not $SkipLogin -and -not $env:CLOUDFLARE_API_TOKEN) {
    Write-Host "Wrangler logout..." -ForegroundColor Yellow
    Push-Location $workerDir
    npx wrangler logout 2>&1
    Pop-Location

    Write-Host "Wrangler login — piliin ang hanahsong2424@gmail.com sa browser." -ForegroundColor Yellow
    Push-Location $workerDir
    npx wrangler login 2>&1
    if ($LASTEXITCODE -ne 0) { throw "wrangler login failed" }
    Pop-Location
}

Push-Location $workerDir
Write-Host "Deploying dl-resolver..." -ForegroundColor Cyan
npx wrangler deploy 2>&1
if ($LASTEXITCODE -ne 0) { throw "dl-resolver deploy failed" }
Pop-Location

Push-Location $ankerDir
if (-not (Test-Path node_modules)) { npm install 2>&1 | Out-Null }

Write-Host "Deploying anker-resolver..." -ForegroundColor Cyan
npx wrangler deploy -c wrangler.anker-resolver.jsonc 2>&1
if ($LASTEXITCODE -ne 0) { throw "anker-resolver deploy failed" }

Write-Host "Deploying anker-dlresolver..." -ForegroundColor Cyan
npx wrangler deploy -c wrangler.anker-dlresolver.jsonc 2>&1
if ($LASTEXITCODE -ne 0) { throw "anker-dlresolver deploy failed" }
Pop-Location

Write-Host "`nAll workers deployed.`n" -ForegroundColor Green
