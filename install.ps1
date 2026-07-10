<#
    QuickPlay - One-paste installer
    Usage (run in PowerShell):
        irm https://cdn.jsdelivr.net/gh/hammerwebsite12/hammerfree@quickplay/install.ps1 | iex

    Direct GitHub raw (if CDN is unavailable):
        irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/quickplay/install.ps1 | iex
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference     = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---- Config -------------------------------------------------------------
$Branch      = 'quickplay'
$Repo        = 'hammerwebsite12/hammerfree'
$ReleaseTag  = 'quickplay-v1.0'
$ScriptRev   = 'quickplay'
$InstallUrl  = "https://cdn.jsdelivr.net/gh/$Repo@$ScriptRev/install.ps1"
$InstallDir  = 'C:\Program Files (x86)\QuickPlay'
$AppName     = 'QuickPlay'
$Version     = '1.0'
$Publisher   = 'QuickPlay'
$Parts       = @('QuickPlay.zip')
$ExtraDirs   = @('cache', 'cache\covers')

# ---- Self-elevate to Administrator --------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host 'Requesting administrator rights...' -ForegroundColor Yellow
    $cmd = "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm $InstallUrl | iex"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $b64
        ) | Out-Null
    } catch {
        Write-Host 'Administrator rights are required. Installation cancelled.' -ForegroundColor Red
    }
    return
}

Write-Host '==============================================' -ForegroundColor Cyan
Write-Host "  Installing $AppName" -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan

$work    = Join-Path $env:TEMP ('quickplay_' + [Guid]::NewGuid().ToString('N'))
$zipPath = Join-Path $work 'QuickPlay.zip'
New-Item -ItemType Directory -Path $work -Force | Out-Null

function Format-Span([double]$seconds) {
    if ($seconds -lt 0 -or [double]::IsInfinity($seconds) -or [double]::IsNaN($seconds)) { return '--:--' }
    $ts = [TimeSpan]::FromSeconds([math]::Round($seconds))
    if ($ts.TotalHours -ge 1) { return ('{0:00}:{1:00}:{2:00}' -f [int]$ts.TotalHours, $ts.Minutes, $ts.Seconds) }
    return ('{0:00}:{1:00}' -f $ts.Minutes, $ts.Seconds)
}

function Get-PartUrls([string]$name) {
    @(
        "https://github.com/$Repo/releases/download/$ReleaseTag/$name",
        "https://raw.githubusercontent.com/$Repo/$Branch/$name",
        "https://github.com/$Repo/raw/$Branch/$name"
    )
}

function Get-RetryWaitSeconds([int]$attempt, [System.Net.WebException]$webEx) {
    $retryAfter = 0
    if ($webEx.Response) { $retryAfter = [int]$webEx.Response.Headers['Retry-After'] }
    if ($retryAfter -gt 0) { return $retryAfter }
    return [math]::Min(120, 15 * $attempt)
}

function Get-FileCurl([string]$url, [string]$dest) {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { return $false }
    Write-Host '   using curl fallback ...' -ForegroundColor DarkGray
    & curl.exe -fL --retry 3 --retry-delay 5 -A 'QuickPlayInstaller/1.0' -o $dest $url 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return $false }
    return (Test-Path $dest) -and ((Get-Item $dest).Length -gt 0)
}

function Get-File($urls, $dest, $label) {
    $urlList  = @($urls)
    $maxTries = 6
    $lastErr  = $null

    for ($try = 1; $try -le $maxTries; $try++) {
        foreach ($url in $urlList) {
            $resp = $null; $rs = $null; $fs = $null
            try {
                $req = [System.Net.HttpWebRequest]::Create($url)
                $req.UserAgent        = 'QuickPlayInstaller/1.0'
                $req.Accept           = 'application/octet-stream,*/*'
                $req.Timeout          = 30000
                $req.ReadWriteTimeout = 120000
                $resp  = $req.GetResponse()
                $total = [int64]$resp.ContentLength
                $rs    = $resp.GetResponseStream()
                $fs    = [System.IO.File]::Create($dest)

                $buf  = New-Object byte[] (262144)
                $read = [int64]0
                $sw   = [System.Diagnostics.Stopwatch]::StartNew()
                $lastMs = -1000.0

                while (($n = $rs.Read($buf, 0, $buf.Length)) -gt 0) {
                    $fs.Write($buf, 0, $n)
                    $read += $n
                    $nowMs = $sw.Elapsed.TotalMilliseconds
                    if (($nowMs - $lastMs) -ge 250 -or $read -eq $total) {
                        $lastMs = $nowMs
                        $secs   = [math]::Max($sw.Elapsed.TotalSeconds, 0.001)
                        $speed  = $read / $secs
                        $spd    = '{0:N1} MB/s' -f ($speed / 1MB)
                        if ($total -gt 0) {
                            $pct = [int][math]::Min(100, ($read / $total) * 100)
                            $eta = if ($speed -gt 0) { Format-Span (($total - $read) / $speed) } else { '--:--' }
                            $status = '{0:N1} / {1:N1} MB   {2}   ETA {3}' -f ($read/1MB), ($total/1MB), $spd, $eta
                            Write-Progress -Activity $label -Status $status -PercentComplete $pct
                        } else {
                            Write-Progress -Activity $label -Status ('{0:N1} MB   {1}' -f ($read/1MB), $spd)
                        }
                    }
                }
                Write-Progress -Activity $label -Completed
                return
            } catch {
                Write-Progress -Activity $label -Completed
                $lastErr = $_
                if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
                $status = 0
                if ($_.Exception -is [System.Net.WebException] -and $_.Exception.Response) {
                    $status = [int]$_.Exception.Response.StatusCode
                }
                if ($status -eq 429) {
                    $wait = Get-RetryWaitSeconds $try $_.Exception
                    Write-Host "   Rate limit (429). Waiting ${wait}s before retry $try/$maxTries ..." -ForegroundColor DarkYellow
                    Start-Sleep -Seconds $wait
                    break
                }
            } finally {
                if ($fs)   { $fs.Close() }
                if ($rs)   { $rs.Close() }
                if ($resp) { $resp.Close() }
            }
        }

        if ($lastErr -and $try -lt $maxTries) {
            $status = 0
            if ($lastErr.Exception -is [System.Net.WebException] -and $lastErr.Exception.Response) {
                $status = [int]$lastErr.Exception.Response.StatusCode
            }
            if ($status -ne 429) {
                foreach ($url in $urlList) {
                    if (Get-FileCurl $url $dest) { return }
                }
                Write-Host "   retry $try/$maxTries ..." -ForegroundColor DarkYellow
                Start-Sleep -Seconds (3 * $try)
            }
            continue
        }
    }

    if ($lastErr) { throw $lastErr }
    throw "Download failed for $label"
}

try {
    Write-Host "Downloading payload ($($Parts.Count) part)..." -ForegroundColor Green
    $i = 0
    foreach ($p in $Parts) {
        $i++
        $label = "Downloading $AppName  -  part $i of $($Parts.Count)  ($p)"
        Write-Host ("  [{0}/{1}] {2}" -f $i, $Parts.Count, $p)
        Get-File (Get-PartUrls $p) $zipPath $label
    }

    Get-Process -Name 'QuickPlay' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Host "Installing to $InstallDir ..." -ForegroundColor Green
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($entry in $zip.Entries) {
            $target = Join-Path $InstallDir $entry.FullName
            if ([string]::IsNullOrEmpty($entry.Name)) {
                New-Item -ItemType Directory -Path $target -Force | Out-Null
                continue
            }
            $parent = Split-Path $target -Parent
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }

    foreach ($d in $ExtraDirs) {
        $dp = Join-Path $InstallDir $d
        if (-not (Test-Path $dp)) { New-Item -ItemType Directory -Path $dp -Force | Out-Null }
    }

    $exePath = Join-Path $InstallDir 'QuickPlay.exe'
    $unPath  = Join-Path $InstallDir 'uninstall.ps1'

    Write-Host 'Creating Desktop shortcut...' -ForegroundColor Green
    # Some PCs redirect Desktop to a broken OneDrive/OneNote path. Prefer a
    # writable folder and never fail the whole install over a shortcut.
    function Get-WritableDesktop {
        $candidates = @(
            [Environment]::GetFolderPath('Desktop'),
            (Join-Path $env:USERPROFILE 'Desktop'),
            [Environment]::GetFolderPath('CommonDesktopDirectory'),
            (Join-Path $env:PUBLIC 'Desktop')
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

        foreach ($dir in $candidates) {
            try {
                if (-not (Test-Path -LiteralPath $dir)) {
                    New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
                }
                $probe = Join-Path $dir ('.qp_write_test_' + [Guid]::NewGuid().ToString('N'))
                [IO.File]::WriteAllText($probe, 'ok')
                Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
                return $dir
            } catch {
                continue
            }
        }
        return $null
    }

    $lnk = $null
    try {
        $desktop = Get-WritableDesktop
        if (-not $desktop) { throw 'No writable Desktop folder found.' }
        $lnk = Join-Path $desktop "$AppName.lnk"
        $wsh = New-Object -ComObject WScript.Shell
        $sc  = $wsh.CreateShortcut($lnk)
        $sc.TargetPath       = $exePath
        $sc.WorkingDirectory = $InstallDir
        $sc.IconLocation     = "$exePath,0"
        $sc.Description      = $AppName
        $sc.Save()
        Write-Host "  Shortcut: $lnk" -ForegroundColor DarkGray
    } catch {
        Write-Host "  Warning: could not create Desktop shortcut ($($_.Exception.Message))" -ForegroundColor Yellow
        Write-Host "  App is still installed. Launch from: $exePath" -ForegroundColor Yellow
        $lnk = $null
    }

    Write-Host 'Registering uninstall entry...' -ForegroundColor Green
    $regKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QuickPlay'
    if (-not (Test-Path $regKey)) { New-Item -Path $regKey -Force | Out-Null }
    $size = [math]::Round(((Get-ChildItem $InstallDir -Recurse -File -ErrorAction SilentlyContinue |
             Measure-Object Length -Sum).Sum / 1KB))
    $unCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$unPath`""
    New-ItemProperty -Path $regKey -Name 'DisplayName'     -Value $AppName    -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'DisplayVersion'  -Value $Version    -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'Publisher'       -Value $Publisher  -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'DisplayIcon'     -Value "$exePath,0" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'InstallLocation' -Value $InstallDir -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'UninstallString'   -Value $unCmd      -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'EstimatedSize'   -Value $size       -PropertyType DWord  -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'NoModify'        -Value 1           -PropertyType DWord  -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'NoRepair'        -Value 1           -PropertyType DWord  -Force | Out-Null

    Write-Host ''
    Write-Host '==============================================' -ForegroundColor Green
    Write-Host "  $AppName installed successfully!" -ForegroundColor Green
    Write-Host "  Location : $InstallDir" -ForegroundColor Green
    if ($lnk) {
        Write-Host "  Shortcut : $lnk" -ForegroundColor Green
    } else {
        Write-Host "  Launch   : $exePath" -ForegroundColor Green
    }
    Write-Host '==============================================' -ForegroundColor Green
}
catch {
    Write-Host ''
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Message -match '429|Too Many Requests|404') {
        Write-Host ''
        Write-Host 'Try again later or install manually from:' -ForegroundColor Yellow
        Write-Host "  https://github.com/$Repo/releases/tag/$ReleaseTag" -ForegroundColor Yellow
    }
    throw
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host 'Press any key to exit...'
try { $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown') } catch {}
