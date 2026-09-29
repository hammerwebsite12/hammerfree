# Set QUICKPLAY_HWID_PAT on astral-dlresolver (required for HWID license checks).
# Cloudflare cannot copy secrets from anker-dlresolver — use the same GitHub PAT
# you used for Server 1/2 Workers (read+write on hammerwebsite12/quickplayusr).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pat = $env:QUICKPLAY_HWID_PAT
if (-not $pat) {
    Write-Host "Paste GitHub PAT (same as anker-dlresolver / dl-resolver), then Enter:" -ForegroundColor Cyan
    $secure = Read-Host -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $pat = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) | Out-Null
}

$pat = ($pat | Out-String).Trim()
if (-not $pat) {
    throw "Empty PAT — cancelled."
}

$pat | npx wrangler secret put QUICKPLAY_HWID_PAT -c wrangler.astral-dlresolver.jsonc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "QUICKPLAY_HWID_PAT set on astral-dlresolver. Retry Server 3 download." -ForegroundColor Green
