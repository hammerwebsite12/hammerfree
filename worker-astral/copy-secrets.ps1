# Upload shared QuickPlay secrets to astral-dlresolver only (does not touch S1/S2 workers).
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SecretsFileParts = @()
)

$SecretsFile = if ($SecretsFileParts.Count -gt 0) {
    ($SecretsFileParts -join ":").Replace("/", "\")
} else {
    ""
}

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$candidates = @(
    $SecretsFile,
    (Join-Path $PSScriptRoot "..\\worker\\.secrets.json"),
    (Join-Path $PSScriptRoot "..\\worker-anker\\.secrets.json"),
    (Join-Path $PSScriptRoot ".secrets.json")
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $candidates -or $candidates.Count -eq 0) {
    Write-Host "No .secrets.json found." -ForegroundColor Red
    Write-Host "Copy secrets to worker\\.secrets.json (see worker-anker\\.secrets.example.json), then rerun." -ForegroundColor Yellow
    exit 1
}

$file = @($candidates)[0]
Write-Host "Using secrets file: $file" -ForegroundColor Cyan

$json = Get-Content $file -Raw | ConvertFrom-Json
$bundle = @{}
foreach ($key in @("APP_TOKEN", "SIGNING_SECRET", "QUICKPLAY_HWID_PAT", "DISCORD_WEBHOOK_URL")) {
    if ($json.PSObject.Properties.Name -contains $key -and "$($json.$key)".Trim()) {
        $bundle[$key] = "$($json.$key)".Trim()
    }
}

$path = Join-Path $env:TEMP "astral-dlresolver-secrets.json"
($bundle | ConvertTo-Json) | Set-Content $path -Encoding utf8

if (-not $bundle.ContainsKey("QUICKPLAY_HWID_PAT")) {
    Write-Host "WARNING: QUICKPLAY_HWID_PAT missing in secrets file." -ForegroundColor Yellow
    Write-Host "Run (same value as anker-dlresolver):" -ForegroundColor Yellow
    Write-Host "  npx wrangler secret put QUICKPLAY_HWID_PAT -c wrangler.astral-dlresolver.jsonc" -ForegroundColor Yellow
}

Write-Host "Uploading to astral-dlresolver..." -ForegroundColor Cyan
npx wrangler secret bulk $path -c wrangler.astral-dlresolver.jsonc

Remove-Item $path -Force -ErrorAction SilentlyContinue
Write-Host "Done. Server 1/2 workers were NOT modified." -ForegroundColor Green
