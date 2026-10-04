# Redeploy dist\QuickPlay.exe to Program Files (requires Admin PowerShell)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root 'dist\QuickPlay.exe'
$dst = 'C:\Program Files (x86)\QuickPlay\QuickPlay.exe'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Re-launching as Administrator...' -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $MyInvocation.MyCommand.Path
    )
    exit
}

Write-Host 'Deploying QuickPlay...' -ForegroundColor Cyan
cmd /c taskkill /IM QuickPlay.exe /F >nul 2>&1
Start-Sleep -Seconds 2
if (-not (Test-Path $src)) { throw "Build not found: $src`nRun .\build-protected.ps1 first." }
$dir = Split-Path $dst -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
[System.IO.File]::Copy($src, $dst, $true)
Get-Item -LiteralPath $dst | Format-List FullName, Length, LastWriteTime
Write-Host 'DEPLOY OK — launch QuickPlay from Desktop or Program Files.' -ForegroundColor Green
if ($Host.Name -eq 'ConsoleHost') {
    Read-Host 'Press Enter to close'
}
