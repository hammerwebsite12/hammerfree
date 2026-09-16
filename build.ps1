# Build single-file EXE for QuickPlay (Web UI, requires Administrator)
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required. Install Python 3.10+ first."
}

python -m pip install -r requirements.txt

python -m PyInstaller --noconfirm --clean QuickPlay.spec

Write-Host ""
Write-Host "Build complete: dist\QuickPlay.exe" -ForegroundColor Green
Write-Host "Note: Windows will show a UAC prompt (Administrator required)." -ForegroundColor Yellow
