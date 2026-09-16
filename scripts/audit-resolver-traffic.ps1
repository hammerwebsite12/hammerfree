# QuickPlay resolver traffic audit — top client IPs + rate-limit KV + daily /sign counts.
# Requires wrangler logged into the hs2424 account (hanahsong2424@gmail.com), OR:
#   $env:CLOUDFLARE_API_TOKEN = "<token from dashboard>"
#   $env:CLOUDFLARE_ACCOUNT_ID = "<account id from Workers overview>"
#
# Usage:
#   .\scripts\audit-resolver-traffic.ps1
#   .\scripts\audit-resolver-traffic.ps1 -Hours 24

param(
    [int]$Hours = 24
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$workerDir = Join-Path $repoRoot "worker"
$ankerDir = Join-Path $repoRoot "worker-anker"

function Get-WranglerToken {
    $cfg = Join-Path $env:APPDATA "xdg.config\.wrangler\config\default.toml"
    if (-not (Test-Path $cfg)) { return $null }
    $line = Get-Content $cfg | Select-String 'oauth_token = "(.*)"'
    if ($line) { return $line.Matches.Groups[1].Value }
    return $null
}

function Invoke-CfGraphQL {
    param([string]$Token, [string]$Query, [hashtable]$Variables = @{})
    $body = @{ query = $Query; variables = $Variables } | ConvertTo-Json -Depth 6 -Compress
    $resp = Invoke-RestMethod -Method Post -Uri "https://api.cloudflare.com/client/v4/graphql" `
        -Headers @{ Authorization = "Bearer $Token" } `
        -ContentType "application/json" -Body $body
    if ($resp.errors) {
        throw ($resp.errors | ForEach-Object { $_.message } | Out-String)
    }
    return $resp.data
}

function Resolve-AccountId {
    param([string]$Token)
    if ($env:CLOUDFLARE_ACCOUNT_ID) { return $env:CLOUDFLARE_ACCOUNT_ID }
    $accounts = (Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts" `
        -Headers @{ Authorization = "Bearer $Token" }).result
    foreach ($acct in $accounts) {
        try {
            $scripts = (Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts/$($acct.id)/workers/scripts" `
                -Headers @{ Authorization = "Bearer $Token" }).result
            $names = @($scripts | ForEach-Object { $_.id })
            if ($names -contains "dl-resolver") {
                return $acct.id
            }
        } catch { }
    }
    return $null
}

function Get-TopWorkerIPs {
    param(
        [string]$Token,
        [string]$AccountId,
        [string]$ScriptName,
        [datetime]$Start,
        [datetime]$End,
        [int]$Limit = 50
    )
    $query = @"
query TopIPs(`$accountTag: String!, `$start: Time!, `$end: Time!, `$script: String!, `$limit: Int!) {
  viewer {
    accounts(filter: { accountTag: `$accountTag }) {
      workersInvocationsAdaptive(
        limit: `$limit
        filter: {
          datetime_geq: `$start
          datetime_leq: `$end
          scriptName: `$script
        }
        orderBy: [sum_requests_DESC]
      ) {
        dimensions { clientIP }
        sum { requests errors }
      }
    }
  }
}
"@
    $vars = @{
        accountTag = $AccountId
        start      = $Start.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        end        = $End.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        script     = $ScriptName
        limit      = $Limit
    }
    $data = Invoke-CfGraphQL -Token $Token -Query $query -Variables $vars
    $rows = $data.viewer.accounts[0].workersInvocationsAdaptive
    return @($rows | ForEach-Object {
            [pscustomobject]@{
                IP       = $_.dimensions.clientIP
                Requests = [int]$_.sum.requests
                Errors   = [int]$_.sum.errors
            }
        })
}

function Get-KvUsageSign {
    param([string]$NamespaceId, [int]$Days = 7)
    $out = @()
    for ($i = 0; $i -lt $Days; $i++) {
        $day = (Get-Date).ToUniversalTime().AddDays(-$i).ToString("yyyy-MM-dd")
        $key = "usage:sign:$day"
        Push-Location $workerDir
        try {
            $val = npx wrangler kv key get $key --namespace-id=$NamespaceId --remote 2>$null
            if ($val) {
                $out += [pscustomobject]@{ Day = $day; SignRequests = [int]$val }
            }
        } finally { Pop-Location }
    }
    return $out
}

Write-Host "`n=== QuickPlay Resolver Traffic Audit (last $Hours h) ===`n" -ForegroundColor Cyan

$token = $env:CLOUDFLARE_API_TOKEN
if (-not $token) { $token = Get-WranglerToken }
if (-not $token) {
    Write-Host "No Cloudflare token. Run: npx wrangler login" -ForegroundColor Red
    Write-Host "Pick hanahsong2424@gmail.com (hs2424 workers account), not monzikmonzik5.`n"
    exit 1
}

$accountId = Resolve-AccountId -Token $token
if (-not $accountId) {
    Write-Host "Could not find dl-resolver on any account visible to this token." -ForegroundColor Red
    Write-Host "Wrangler is probably on the wrong account. Run:" -ForegroundColor Yellow
    Write-Host "  cd `"$workerDir`"" -ForegroundColor White
    Write-Host "  npx wrangler login" -ForegroundColor White
    Write-Host "Then select hanahsong2424@gmail.com / hs2424.`n"
    exit 1
}

$acctName = (Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts/$accountId" `
    -Headers @{ Authorization = "Bearer $token" }).result.name
Write-Host "Account: $acctName ($accountId)`n" -ForegroundColor Green

$end = Get-Date
$start = $end.AddHours(-$Hours)

foreach ($worker in @("dl-resolver", "anker-dlresolver", "anker-resolver")) {
    Write-Host "--- $worker ---" -ForegroundColor Yellow
    try {
        $ips = Get-TopWorkerIPs -Token $token -AccountId $accountId -ScriptName $worker -Start $start -End $end -Limit 30
        if (-not $ips -or $ips.Count -eq 0) {
            Write-Host "  (no analytics rows - worker idle or observability lag)`n"
            continue
        }
        $ips | Format-Table -AutoSize
        $hot = $ips | Where-Object { $_.Requests -ge 60 }
        if ($hot) {
            Write-Host "  [!] Suspicious (>= 60 req / $Hours h, rate limit is 20/min):" -ForegroundColor Red
            $hot | Format-Table -AutoSize
        }
        Write-Host ""
    } catch {
        Write-Host "  GraphQL error: $($_.Exception.Message)`n" -ForegroundColor DarkYellow
    }
}

Write-Host "--- dl-resolver daily /sign counts (KV) ---" -ForegroundColor Yellow
Get-KvUsageSign -NamespaceId "54f59a4ca729454598ce4fbd171578b9" | Format-Table -AutoSize

Write-Host "--- Current rate-limit buckets (sample) ---" -ForegroundColor Yellow
Write-Host "Listing rl:* keys from PZ_KV (may be empty if no traffic this minute)...`n"
Push-Location $workerDir
try {
    npx wrangler kv key list --namespace-id=54f59a4ca729454598ce4fbd171578b9 --prefix="rl:" --remote 2>&1 |
        Select-Object -First 40
} finally { Pop-Location }

Write-Host "`nDone. For live tail: cd worker; npx wrangler tail dl-resolver`n" -ForegroundColor Cyan
