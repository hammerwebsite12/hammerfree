# Deploy QuickPlay Anker Workers (resolver + dlresolver).
# Usage:
#   .\deploy-anker.ps1                 # deploy both (default)
#   .\deploy-anker.ps1 -DlOnly         # dlresolver only (snapshot / download path)
#   .\deploy-anker.ps1 -ReLogin        # wrangler logout + login, then deploy
#   .\deploy-anker.ps1 -ReLogin -DlOnly
#
# Non-interactive CI: set CLOUDFLARE_API_TOKEN (skips OAuth login prompt).
param(
    [switch]$ReLogin,
    [switch]$DlOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$logDir = Join-Path (Split-Path $PSScriptRoot -Parent) "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir ("anker-deploy-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

function Write-Log {
    param([string]$Message, [string]$Color = "White")
    $line = "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor $Color
}

function Test-WranglerAuth {
    $who = npx wrangler whoami 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { return $false }
    if ($who -match "not authenticated") { return $false }
    return $true
}

Write-Log "QuickPlay Anker worker deploy started" Cyan
Write-Log "Log file: $logFile" DarkGray

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Write-Log "Node/npx not found. Install Node.js first." Red
    exit 1
}

if (-not (Test-Path node_modules)) {
    Write-Log "Running npm install..." Cyan
    npm install 2>&1 | Tee-Object -FilePath $logFile -Append
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

foreach ($file in @("wrangler.anker-resolver.jsonc", "wrangler.anker-dlresolver.jsonc")) {
    $text = Get-Content $file -Raw
    if ($text -match "REPLACE_WITH_ANKER_KV_ID") {
        Write-Log "KV namespace not configured in $file - run .\setup.ps1 first." Red
        exit 1
    }
}

if ($ReLogin) {
    if ($env:CLOUDFLARE_API_TOKEN) {
        Write-Log "CLOUDFLARE_API_TOKEN is set - skipping logout/login." Yellow
    } else {
        $accountCache = Join-Path $PSScriptRoot "node_modules\.cache\wrangler\wrangler-account.json"
        if (Test-Path $accountCache) {
            Remove-Item -LiteralPath $accountCache -Force
            Write-Log "Cleared cached wrangler account selection." Yellow
        }

        Write-Log "Wrangler logout..." Yellow
        npx wrangler logout 2>&1 | Tee-Object -FilePath $logFile -Append

        Write-Log "Wrangler login - complete auth in the browser window." Yellow
        npx wrangler login 2>&1 | Tee-Object -FilePath $logFile -Append
        if ($LASTEXITCODE -ne 0) {
            Write-Log "wrangler login failed." Red
            exit $LASTEXITCODE
        }
    }
}

if (-not $env:CLOUDFLARE_API_TOKEN -and -not (Test-WranglerAuth)) {
    Write-Log "Not authenticated. Re-run with -ReLogin or set CLOUDFLARE_API_TOKEN." Red
    exit 1
}

$who = npx wrangler whoami 2>&1 | Out-String
Write-Log ("Authenticated as:`n" + $who.Trim()) Green

$targets = @()
if ($DlOnly) {
    $targets = @("wrangler.anker-dlresolver.jsonc")
} else {
    $targets = @("wrangler.anker-resolver.jsonc", "wrangler.anker-dlresolver.jsonc")
}

foreach ($config in $targets) {
    $name = if ($config -match "anker-dlresolver") { "anker-dlresolver" } else { "anker-resolver" }
    Write-Log "Deploying $name ($config)..." Cyan
    npx wrangler deploy -c $config 2>&1 | Tee-Object -FilePath $logFile -Append
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Deploy failed for $name." Red
        exit $LASTEXITCODE
    }
    Write-Log "Deployed $name successfully." Green
}

Write-Log "All requested Anker workers deployed." Green
exit 0
