$ErrorActionPreference = 'Stop'
$src = 'D:\My Drive\Workspace\plazipanker\dist\QuickPlay.exe'
$stage = Join-Path $env:TEMP 'QuickPlay_deploy.exe'
$dst = 'C:\Program Files (x86)\QuickPlay\QuickPlay.exe'
$log = 'D:\My Drive\Workspace\plazipanker\_deploy_now.log'

try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    taskkill /IM QuickPlay.exe /F 2>$null | Out-Null
    $ErrorActionPreference = $prevEap
    Start-Sleep -Seconds 3

    if (-not (Test-Path $src)) { throw "Missing source: $src" }

    Copy-Item -LiteralPath $src -Destination $stage -Force
    $srcHash = (Get-FileHash -LiteralPath $src).Hash
    $stageHash = (Get-FileHash -LiteralPath $stage).Hash
    if ($srcHash -ne $stageHash) { throw "Stage hash mismatch" }

    Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'QuickPlay\_runtime') -Recurse -Force -ErrorAction SilentlyContinue

    $destDir = Split-Path $dst -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    Copy-Item -LiteralPath $stage -Destination $dst -Force
    $dstHash = (Get-FileHash -LiteralPath $dst).Hash
    if ($dstHash -ne $srcHash) { throw "Deploy hash mismatch" }

    $info = Get-Item -LiteralPath $dst
    @(
        'DEPLOY OK',
        "Source: $src",
        "Stage:  $stage",
        "Dest:   $($info.FullName)",
        "Size:   $($info.Length)",
        "Time:   $($info.LastWriteTime)",
        "Hash:   $dstHash"
    ) | Set-Content -Path $log -Encoding UTF8
} catch {
    "DEPLOY FAILED: $($_.Exception.Message)" | Set-Content -Path $log -Encoding UTF8
    exit 1
}
