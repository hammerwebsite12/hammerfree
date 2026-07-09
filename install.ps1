<#
    Hammer 3.8 - One-paste installer
    Usage (run in PowerShell):
        irm https://cdn.jsdelivr.net/gh/hammerwebsite12/hammerfree@installer/install.ps1 | iex

    Direct GitHub raw (if CDN is unavailable):
        irm https://raw.githubusercontent.com/hammerwebsite12/hammerfree/installer/install.ps1 | iex
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference     = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---- Config -------------------------------------------------------------
$Branch      = 'installer'
$Repo        = 'hammerwebsite12/hammerfree'
$ReleaseTag  = 'v3.8'
$ScriptRev   = 'installer'
$InstallUrls = @(
    "https://cdn.jsdelivr.net/gh/$Repo@$ScriptRev/install.ps1",
    "https://raw.githubusercontent.com/$Repo/$Branch/install.ps1"
)
$InstallUrl  = $InstallUrls[0]
$InstallDir = "C:\Program Files (x86)\Hammer"
$AppName    = 'Hammer 3.8'
$Version    = '3.8'
$Publisher  = 'Hammer'
$Parts      = @('Hammer-3.8.zip.001', 'Hammer-3.8.zip.002')

# ---- Self-elevate to Administrator --------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Requesting administrator rights..." -ForegroundColor Yellow
    $cmd = "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm $InstallUrl | iex"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$b64
        ) | Out-Null
    } catch {
        Write-Host "Administrator rights are required. Installation cancelled." -ForegroundColor Red
    }
    return
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Installing $AppName" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---- Workspace ----------------------------------------------------------
$work = Join-Path $env:TEMP ("hammer38_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -Force | Out-Null
$zipPath = Join-Path $work 'Hammer-3.8.zip'

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
    if ($webEx.Response) {
        $retryAfter = [int]$webEx.Response.Headers['Retry-After']
    }
    if ($retryAfter -gt 0) { return $retryAfter }
    return [math]::Min(120, 15 * $attempt)
}

function Get-FileCurl([string]$url, [string]$dest, [string]$label) {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { return $false }
    Write-Host "   using curl fallback ..." -ForegroundColor DarkGray
    $code = & curl.exe -fL --retry 3 --retry-delay 5 -A 'HammerInstaller/3.8' -o $dest $url 2>&1
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
                $req.UserAgent        = 'HammerInstaller/3.8'
                $req.Accept           = 'application/octet-stream,*/*'
                $req.Timeout          = 30000
                $req.ReadWriteTimeout = 120000
                $resp  = $req.GetResponse()
                $total = [int64]$resp.ContentLength
                $rs    = $resp.GetResponseStream()
                $fs    = [System.IO.File]::Create($dest)

                $buf  = New-Object byte[] (262144)   # 256 KB
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
                    Write-Host "   GitHub rate limit (429). Waiting ${wait}s before retry $try/$maxTries ..." -ForegroundColor DarkYellow
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
                    if (Get-FileCurl $url $dest $label) { return }
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
    # ---- Download parts -------------------------------------------------
    Write-Host "Downloading payload ($($Parts.Count) parts)..." -ForegroundColor Green
    $partFiles = @()
    $i = 0
    foreach ($p in $Parts) {
        $i++
        $dest  = Join-Path $work $p
        $label = "Downloading $AppName  -  part $i of $($Parts.Count)  ($p)"
        Write-Host ("  [{0}/{1}] {2}" -f $i, $Parts.Count, $p)
        Get-File (Get-PartUrls $p) $dest $label
        $partFiles += $dest
    }

    # ---- Reassemble zip -------------------------------------------------
    Write-Host "Reassembling package..." -ForegroundColor Green
    $out = [System.IO.File]::Create($zipPath)
    try {
        foreach ($pf in $partFiles) {
            $in = [System.IO.File]::OpenRead($pf)
            try { $in.CopyTo($out) } finally { $in.Close() }
        }
    } finally { $out.Close() }

    # ---- Stop running Hammer --------------------------------------------
    Get-Process -Name 'Hammer','SteamDbBridgeHost','packer' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    # ---- Extract --------------------------------------------------------
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

    $exePath = Join-Path $InstallDir 'Hammer.exe'
    $icoPath = Join-Path $InstallDir 'hammer.ico'
    $unPath  = Join-Path $InstallDir 'Uninstall.exe'

    # ---- Desktop shortcut ----------------------------------------------
    Write-Host "Creating Desktop shortcut..." -ForegroundColor Green
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrEmpty($desktop)) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
    $lnk = Join-Path $desktop "$AppName.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $sc  = $wsh.CreateShortcut($lnk)
    $sc.TargetPath       = $exePath
    $sc.WorkingDirectory = $InstallDir
    if (Test-Path $icoPath) { $sc.IconLocation = $icoPath }
    $sc.Description      = $AppName
    $sc.Save()

    # ---- Control Panel uninstall entry ----------------------------------
    Write-Host "Registering uninstall entry..." -ForegroundColor Green
    $regKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Hammer'
    if (-not (Test-Path $regKey)) { New-Item -Path $regKey -Force | Out-Null }
    $size = [math]::Round(((Get-ChildItem $InstallDir -Recurse -File -ErrorAction SilentlyContinue |
             Measure-Object Length -Sum).Sum / 1KB))
    New-ItemProperty -Path $regKey -Name 'DisplayName'     -Value $AppName    -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'DisplayVersion'  -Value $Version    -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'Publisher'       -Value $Publisher  -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'DisplayIcon'     -Value $icoPath    -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'InstallLocation' -Value $InstallDir -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'UninstallString' -Value ('"{0}"' -f $unPath) -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'EstimatedSize'   -Value $size       -PropertyType DWord  -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'NoModify'        -Value 1           -PropertyType DWord  -Force | Out-Null
    New-ItemProperty -Path $regKey -Name 'NoRepair'        -Value 1           -PropertyType DWord  -Force | Out-Null

    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "  $AppName installed successfully!" -ForegroundColor Green
    Write-Host "  Location : $InstallDir" -ForegroundColor Green
    Write-Host "  Shortcut : $lnk" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Message -match '429|Too Many Requests') {
        Write-Host ""
        Write-Host "Download rate-limited. Try again in a few minutes, or install manually from:" -ForegroundColor Yellow
        Write-Host "  https://github.com/$Repo/releases/tag/$ReleaseTag" -ForegroundColor Yellow
    }
    throw
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Press any key to exit..."
try { $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown') } catch {}
