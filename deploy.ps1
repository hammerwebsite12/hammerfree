# Publish QuickPlay 2.1 installer to hammerwebsite12/hammerfree (quickplay branch)
# Requires: gh auth login as an account with push access to hammerwebsite12/hammerfree
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host 'Checking GitHub access...' -ForegroundColor Cyan
$perm = gh api repos/hammerwebsite12/hammerfree --jq .permissions.push
if ($perm -ne 'true') {
    throw "Current gh account cannot push to hammerwebsite12/hammerfree. Run: gh auth login"
}

$token = gh auth token
git remote set-url origin "https://x-access-token:${token}@github.com/hammerwebsite12/hammerfree.git"
git push origin quickplay

Write-Host 'Creating GitHub release quickplay-v2.1...' -ForegroundColor Cyan
gh release view quickplay-v2.1 --repo hammerwebsite12/hammerfree 2>$null
if ($LASTEXITCODE -eq 0) {
    gh release upload quickplay-v2.1 QuickPlay.zip --repo hammerwebsite12/hammerfree --clobber
} else {
    gh release create quickplay-v2.1 QuickPlay.zip `
        --repo hammerwebsite12/hammerfree `
        --title "QuickPlay v2.1" `
        --notes "QuickPlay 2.1 dual-server build (Server 1 PlayZip + Server 2 AnkerGames). Fixes Anker-branded extract folder deletion. PyArmor-protected."
}

Write-Host 'Done. Users can install with:' -ForegroundColor Green
Write-Host '  irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex' -ForegroundColor Yellow
