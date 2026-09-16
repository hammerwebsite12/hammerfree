$ErrorActionPreference = 'Stop'
$src = 'D:\My Drive\Workspace\plazipanker\dist\QuickPlay.exe'
$dst = 'C:\Program Files (x86)\QuickPlay\QuickPlay.exe'
$log = 'D:\My Drive\Workspace\plazipanker\_deploy_now.log'

try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    taskkill /IM QuickPlay.exe /F 2>$null | Out-Null
    $ErrorActionPreference = $prevEap
    $ErrorActionPreference = $prevEap
    Start-Sleep -Seconds 2
    if (-not (Test-Path $src)) { throw "Missing source: $src" }
    $destDir = Split-Path $dst -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $info = Get-Item -LiteralPath $dst
    @(
        'DEPLOY OK',
        "Source: $src",
        "Dest:   $($info.FullName)",
        "Size:   $($info.Length)",
        "Time:   $($info.LastWriteTime)"
    ) | Set-Content -Path $log -Encoding UTF8
} catch {
    "DEPLOY FAILED: $($_.Exception.Message)" | Set-Content -Path $log -Encoding UTF8
    exit 1
}
