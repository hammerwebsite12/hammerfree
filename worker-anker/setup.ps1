# Create dedicated KV namespace and patch wrangler configs.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    throw "Node/npx required."
}

Write-Host "Creating ANKER_KV namespace..." -ForegroundColor Cyan
$out = npx wrangler kv namespace create ANKER_KV 2>&1 | Out-String
Write-Host $out

if ($out -notmatch 'id = "([a-f0-9]+)"') {
    throw "Could not parse KV namespace id. Run: npx wrangler kv namespace create ANKER_KV"
}
$kvId = $Matches[1]
Write-Host "ANKER_KV id: $kvId" -ForegroundColor Green

foreach ($file in @("wrangler.anker-resolver.jsonc", "wrangler.anker-dlresolver.jsonc")) {
    $path = Join-Path $PSScriptRoot $file
    $text = Get-Content $path -Raw
    $text = $text -replace "REPLACE_WITH_ANKER_KV_ID", $kvId
    Set-Content -Path $path -Value $text -NoNewline
    Write-Host "Updated $file" -ForegroundColor Green
}

Write-Host ""
Write-Host "Next: set secrets (see README.md), then npm run deploy:all" -ForegroundColor Yellow
