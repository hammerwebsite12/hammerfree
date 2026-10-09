# Publish QuickPlay 2.8.1-beta installer to hammerwebsite12/hammerfree (quickplay-beta branch + release)
# Does NOT modify the stable quickplay branch.
# Requires: gh auth login with push access to hammerwebsite12/hammerfree
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$repoRoot = Split-Path $PSScriptRoot -Parent
$worktree = Join-Path $repoRoot '_qp-quickplay-beta-publish'
$betaBranch = 'quickplay-beta'

Write-Host 'Checking GitHub access...' -ForegroundColor Cyan
$perm = gh api repos/hammerwebsite12/hammerfree --jq .permissions.push
if ($perm -ne 'true') {
    throw "Current gh account cannot push to hammerwebsite12/hammerfree. Run: gh auth login"
}

if (-not (Test-Path (Join-Path $PSScriptRoot 'QuickPlay.zip'))) {
    throw 'Missing installer-publish-beta\QuickPlay.zip — run pack-beta.ps1 first (needs dist\QuickPlay.exe).'
}

Set-Location $repoRoot
Write-Host "Fetching hammerfree-install branches..." -ForegroundColor Cyan
git fetch hammerfree-install quickplay

if (Test-Path $worktree) {
    git worktree remove --force $worktree 2>$null
}

$betaRemote = git ls-remote --heads hammerfree-install "refs/heads/$betaBranch"
if ($betaRemote) {
    git fetch hammerfree-install $betaBranch
    git worktree add $worktree hammerfree-install/$betaBranch
} else {
    Write-Host "Creating new branch $betaBranch from quickplay..." -ForegroundColor Cyan
    git worktree add -b $betaBranch $worktree hammerfree-install/quickplay
}
Set-Location $worktree
git checkout $betaBranch 2>$null

Copy-Item -Path (Join-Path $PSScriptRoot '*') -Destination $worktree -Force -Exclude '.git'

git add QuickPlay.zip README-quickplay-beta.md changelog-beta.txt deploy-beta.ps1 install.ps1
$status = git status --porcelain
if ($status) {
    $ver = (Select-String -Path (Join-Path $worktree 'install.ps1') -Pattern '\$Version\s*=\s*''([^'']+)''' | Select-Object -First 1).Matches.Groups[1].Value
    git commit -m "Ship QuickPlay $ver beta installer (quickplay-beta branch)."
    $token = gh auth token
    git remote set-url origin "https://x-access-token:${token}@github.com/hammerwebsite12/hammerfree.git"
    git push -u hammerfree-install $betaBranch
} else {
    Write-Host "No installer file changes to push on $betaBranch." -ForegroundColor Yellow
}

Set-Location $PSScriptRoot
$tagLine = Select-String -Path 'install.ps1' -Pattern "ReleaseTag\s*=\s*'([^']+)'" | Select-Object -First 1
$releaseTag = if ($tagLine) { $tagLine.Matches.Groups[1].Value } else { 'quickplay-v2.8.1-beta' }

Write-Host "Uploading release asset $releaseTag..." -ForegroundColor Cyan
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
gh release view $releaseTag --repo hammerwebsite12/hammerfree 2>$null | Out-Null
$releaseExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
$notes = "QuickPlay 2.8.1-beta test installer. One-paste: refs/heads/quickplay-beta/install.ps1 (stable remains quickplay branch)."
if ($releaseExists) {
    gh release upload $releaseTag QuickPlay.zip --repo hammerwebsite12/hammerfree --clobber
} else {
    gh release create $releaseTag QuickPlay.zip `
        --repo hammerwebsite12/hammerfree `
        --title "QuickPlay $releaseTag" `
        --notes $notes `
        --prerelease
}

Set-Location $repoRoot
if (Test-Path $worktree) {
    git worktree remove --force $worktree 2>$null
}

Write-Host 'Done. Beta testers install with:' -ForegroundColor Green
Write-Host '  irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/refs/heads/quickplay-beta/install.ps1 | iex' -ForegroundColor Yellow
Write-Host 'Stable (unchanged):' -ForegroundColor DarkGray
Write-Host '  irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/refs/heads/quickplay/install.ps1 | iex' -ForegroundColor DarkGray
