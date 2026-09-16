# Publish QuickPlay 2.7.4 installer to hammerwebsite12/hammerfree (quickplay branch)
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

Write-Host 'Creating GitHub release quickplay-v2.7.4...' -ForegroundColor Cyan
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
gh release view quickplay-v2.7.4 --repo hammerwebsite12/hammerfree 2>$null | Out-Null
$releaseExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if ($releaseExists) {
    gh release upload quickplay-v2.7.4 QuickPlay.zip --repo hammerwebsite12/hammerfree --clobber
} else {
    gh release create quickplay-v2.7.4 QuickPlay.zip `
        --repo hammerwebsite12/hammerfree `
        --title "QuickPlay v2.7.4" `
        --notes "QuickPlay 2.7.4 (2026-08-31): First-run activation uses themed in-app modal with Copy Code (no tkinter). PyArmor-protected build."
}

Write-Host 'Done. Users can install with:' -ForegroundColor Green
Write-Host '  irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex' -ForegroundColor Yellow
