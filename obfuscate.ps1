# Obfuscate QuickPlay licensing / API modules with PyArmor (trial-safe subset).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "client_secrets.py")) {
    throw "client_secrets.py is required for a protected build. Copy from client_secrets.example.py."
}

python -m pip install pyarmor pyinstaller -q

$out = Join-Path $PSScriptRoot "build\pyarmor"
if (Test-Path $out) {
    Remove-Item -Recurse -Force $out
}
$stagingRoot = Join-Path $PSScriptRoot "build\staging"
if (Test-Path $stagingRoot) {
    Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
}

$modules = @(
    "client_secrets.py",
    "hwid_obfuscation.py",
    "device_fingerprint.py",
    "hardware_snapshot.py",
    "license_manager.py",
    "playzip_api.py",
    "anker\anker_api.py"
)

Write-Host "Obfuscating $($modules.Count) modules..." -ForegroundColor Cyan
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
pyarmor gen -O $out --obf-code 1 @modules 2>&1 | ForEach-Object { Write-Host $_ }
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($code -ne 0) { throw "PyArmor obfuscation failed (exit $code)." }

$obfAnkerPkg = Join-Path $out "anker"
New-Item -ItemType Directory -Path $obfAnkerPkg -Force | Out-Null
$ankerNames = @("anker_api.py")
foreach ($name in $ankerNames) {
    foreach ($candidate in @(
        (Join-Path $out $name),
        (Join-Path $out "anker\$name")
    )) {
        if (Test-Path $candidate) {
            Move-Item -Force $candidate (Join-Path $obfAnkerPkg $name)
        }
    }
}

Write-Host "Obfuscated output: build\pyarmor" -ForegroundColor Green
