# Build protected QuickPlay.exe (PyArmor + PyInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required. Install Python 3.10+ first."
}

if (-not (Test-Path "quickplay.ico")) {
    throw "quickplay.ico is required for the build."
}

python -m pip install -r requirements.txt

& "$PSScriptRoot\obfuscate.ps1"

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
taskkill /IM QuickPlay.exe /F 2>$null | Out-Null
$ErrorActionPreference = $prevEap

python -m PyInstaller --noconfirm --clean release.spec

Write-Host ""
Write-Host "Protected build complete: dist\QuickPlay.exe" -ForegroundColor Green
Write-Host "Licensing/API modules are PyArmor obfuscated." -ForegroundColor Yellow
