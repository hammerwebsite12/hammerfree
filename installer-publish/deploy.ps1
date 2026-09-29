# Publish QuickPlay installer to hammerwebsite12/hammerfree (quickplay branch + release asset)
# Requires: gh auth login as an account with push access to hammerwebsite12/hammerfree
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$repoRoot = Split-Path $PSScriptRoot -Parent
$worktree = Join-Path $repoRoot '_qp-quickplay-publish'

Write-Host 'Checking GitHub access...' -ForegroundColor Cyan
$perm = gh api repos/hammerwebsite12/hammerfree --jq .permissions.push
if ($perm -ne 'true') {
    throw "Current gh account cannot push to hammerwebsite12/hammerfree. Run: gh auth login"
}

if (-not (Test-Path (Join-Path $PSScriptRoot 'QuickPlay.zip'))) {
    throw 'Missing installer-publish\QuickPlay.zip — run build-protected.ps1 and Compress-Archive dist\QuickPlay.exe first.'
}

Write-Host 'Fetching origin/quickplay...' -ForegroundColor Cyan
Set-Location $repoRoot
git fetch origin quickplay
if (Test-Path $worktree) {
    git worktree remove --force $worktree 2>$null
}
git worktree add $worktree origin/quickplay
Set-Location $worktree
git checkout quickplay 2>$null

Copy-Item -Path (Join-Path $PSScriptRoot '*') -Destination $worktree -Force -Exclude '.git'

git add QuickPlay.zip README-quickplay.md changelog.txt deploy.ps1 install.ps1
$status = git status --porcelain
if ($status) {
    $ver = (Select-String -Path (Join-Path $worktree 'install.ps1') -Pattern '\$Version\s*=\s*''([^'']+)''' | Select-Object -First 1).Matches.Groups[1].Value
    git commit -m "Ship QuickPlay $ver installer (install.ps1 + QuickPlay.zip)."
    $token = gh auth token
    git remote set-url origin "https://x-access-token:${token}@github.com/hammerwebsite12/hammerfree.git"
    git push origin quickplay
} else {
    Write-Host 'No installer file changes to push on quickplay.' -ForegroundColor Yellow
}

Set-Location $PSScriptRoot
$tagLine = Select-String -Path 'install.ps1' -Pattern "ReleaseTag\s*=\s*'([^']+)'" | Select-Object -First 1
$releaseTag = if ($tagLine) { $tagLine.Matches.Groups[1].Value } else { 'quickplay-v2.7.9' }

Write-Host "Uploading release asset $releaseTag..." -ForegroundColor Cyan
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
gh release view $releaseTag --repo hammerwebsite12/hammerfree 2>$null | Out-Null
$releaseExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
$notes = "QuickPlay installer payload refresh ($releaseTag). See quickplay branch install.ps1."
if ($releaseExists) {
    gh release upload $releaseTag QuickPlay.zip --repo hammerwebsite12/hammerfree --clobber
} else {
    gh release create $releaseTag QuickPlay.zip `
        --repo hammerwebsite12/hammerfree `
        --title "QuickPlay $releaseTag" `
        --notes $notes
}

Set-Location $repoRoot
git worktree remove --force $worktree 2>$null

Write-Host 'Done. Users can install with:' -ForegroundColor Green
Write-Host '  irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex' -ForegroundColor Yellow
