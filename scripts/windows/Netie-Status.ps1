#Requires -Version 5.1
<#
.SYNOPSIS
  Read-only health check for the Netie stack. Says exactly which service is down.

.DESCRIPTION
  Start-NetieStack.ps1 starts things. This only looks, and never starts anything,
  so it is safe to run at any time.

  Ports and health endpoints are kept identical to Start-NetieStack.ps1:
    Cortex engine   :8010  /health
    OpenVault API   :5000  /api/healthz
    OpenVault UI    :3010  /
    AirGPT          :8765  /api/info
    Netie Space     desktop process, no port

  It also runs asset checks against the artifact the *browser* receives over HTTP,
  not the copy on disk. On 2026-07-31 AirGPT's index.html was written truncated at
  exactly 512 KiB; the 261 KB inline <script> lost its closing tag, every function
  in it became undefined, and the UI sat on "Local . syncing..." forever with the
  backend answering 200 on every endpoint. Port checks alone called that healthy.

.PARAMETER Deep
  Additionally extract the inline script and run `node --check` on it. Needs node
  on PATH. Catches syntax errors that balanced tags would miss.

.OUTPUTS
  Exit code 0 when everything is up and all asset checks pass, 1 otherwise,
  so this can gate a script.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Netie-Status.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Netie-Status.ps1 -Deep
#>
param(
  [int]$CortexPort = 8010,
  [int]$OpenVaultPort = 5000,
  [int]$OpenVaultUiPort = 3010,
  [int]$AirGptPort = 8765,
  [string]$OpenVaultRoot = "D:\OpenVault",
  [switch]$Deep,
  [switch]$SelfTest
)

# Not "Stop": a probe failure is a result to report, not a reason to quit.
$ErrorActionPreference = "Continue"

function Test-PortListening([int]$Port) {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return [bool]$c
}

function Get-ServiceState {
  param([string]$Name, [int]$Port, [string]$Path)

  $state = [ordered]@{
    Name = $Name; Port = $Port; Url = "http://127.0.0.1:$Port$Path"
    Status = "DOWN"; Detail = ""; Ms = 0; Content = $null
  }

  if (-not (Test-PortListening $Port)) {
    $state.Detail = "nothing listening on :$Port"
    return [pscustomobject]$state
  }

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $r = Invoke-WebRequest -Uri $state.Url -UseBasicParsing -TimeoutSec 5
    $sw.Stop()
    $state.Ms = [int]$sw.ElapsedMilliseconds
    $state.Content = $r.Content
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
      $state.Status = "UP"
      $state.Detail = "HTTP $([int]$r.StatusCode)"
    } else {
      $state.Status = "SICK"
      $state.Detail = "HTTP $([int]$r.StatusCode)"
    }
  } catch {
    $sw.Stop()
    $state.Ms = [int]$sw.ElapsedMilliseconds
    $state.Status = "SICK"
    # Port is open but the endpoint is unhappy - that is a different bug from DOWN.
    $code = $null
    if ($_.Exception.PSObject.Properties.Name -contains "Response" -and $_.Exception.Response) {
      try { $code = [int]$_.Exception.Response.StatusCode } catch { }
    }
    if ($code) { $state.Detail = "HTTP $code" }
    else { $state.Detail = "port open, no HTTP reply: " + $_.Exception.Message }
  }
  return [pscustomobject]$state
}

function Test-HtmlIntact {
  param([string]$Label, [string]$Html)

  $result = [ordered]@{ Label = $Label; Status = "FAIL"; Detail = "" }

  if ($null -eq $Html -or $Html.Length -eq 0) {
    $result.Detail = "empty response"
    return [pscustomobject]$result
  }

  $bytes = [System.Text.Encoding]::UTF8.GetByteCount($Html)
  $opens = ([regex]::Matches($Html, "<script", "IgnoreCase")).Count
  $closes = ([regex]::Matches($Html, "</script", "IgnoreCase")).Count
  $endsOk = $Html.TrimEnd().EndsWith("</html>", [System.StringComparison]::OrdinalIgnoreCase)

  $problems = @()
  if ($opens -ne $closes) { $problems += "$opens <script> vs $closes </script> - unterminated inline script" }
  if (-not $endsOk) { $problems += "does not end with </html> - file is truncated" }

  # Exact power-of-two length is the signature of a capped/partial write.
  if ($bytes -ge 65536 -and (($bytes -band ($bytes - 1)) -eq 0)) {
    $problems += "length is exactly $bytes bytes (a power of two) - almost certainly a truncated write"
  }

  if ($problems.Count -eq 0) {
    $result.Status = "OK"
    $result.Detail = "$bytes bytes, $opens script tags balanced, ends with </html>"
  } else {
    $result.Detail = ($problems -join "; ")
  }
  return [pscustomobject]$result
}

function Test-InlineScriptSyntax {
  param([string]$Label, [string]$Html)

  $result = [ordered]@{ Label = $Label; Status = "FAIL"; Detail = "" }

  $node = Get-Command node -ErrorAction SilentlyContinue
  if (-not $node) {
    # R-0002: a skipped check is a failing check. Say so rather than printing OK.
    $result.Detail = "node not on PATH - syntax check could not run"
    return [pscustomobject]$result
  }

  # Largest inline <script> block (the one that carries the app).
  $rx = [regex]::new("<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", "Singleline, IgnoreCase")
  $best = ""
  foreach ($m in $rx.Matches($Html)) {
    if ($m.Groups[1].Value.Length -gt $best.Length) { $best = $m.Groups[1].Value }
  }
  if ($best.Length -eq 0) {
    $result.Detail = "no inline <script> block found"
    return [pscustomobject]$result
  }

  $tmp = Join-Path $env:TEMP ("netie-status-" + [System.Guid]::NewGuid().ToString("N") + ".js")
  try {
    Set-Content -Path $tmp -Value $best -Encoding utf8
    $err = & node --check $tmp 2>&1
    if ($LASTEXITCODE -eq 0) {
      $result.Status = "OK"
      $result.Detail = "inline script parses ($($best.Length) chars)"
    } else {
      $first = ($err | Select-Object -First 4) -join " | "
      $result.Detail = "inline script does NOT parse: $first"
    }
  } finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  }
  return [pscustomobject]$result
}

if ($SelfTest) {
  # R-0007: a gate you have never seen fail is not a gate. These fixtures prove the
  # asset check actually rejects the shapes it claims to catch.
  Write-Host ""
  Write-Host "======== Netie-Status self-test ========" -ForegroundColor Cyan
  $good = "<html><body><script src='x.js'></script><script>var a=1;</script></body></html>"

  # A page that is well-formed in every other way but lands on exactly 512 KiB.
  # Padded by computation, not by a hand-counted constant - getting that arithmetic
  # wrong is what made this very case report a false PASS on first run.
  $pow2Prefix = "<html><body><script>/*"
  $pow2Suffix = "*/</script></body></html>"
  $pow2Pad = 524288 - $pow2Prefix.Length - $pow2Suffix.Length
  $pow2Html = $pow2Prefix + ("x" * $pow2Pad) + $pow2Suffix
  if ($pow2Html.Length -ne 524288) { throw "self-test fixture is $($pow2Html.Length) bytes, expected 524288" }

  $cases = @(
    @{ Name = "intact page";              Html = $good;                                    Expect = "OK" }
    @{ Name = "unterminated script";      Html = "<html><body><script>var a=1;</body></html>"; Expect = "FAIL" }
    @{ Name = "truncated, no </html>";    Html = "<html><body><script>var a=1;</script>";   Expect = "FAIL" }
    @{ Name = "empty response";           Html = "";                                        Expect = "FAIL" }
    @{ Name = "exactly 512 KiB (today's bug)"; Html = $pow2Html; Expect = "FAIL" }
  )
  $failed = 0
  foreach ($c in $cases) {
    $r = Test-HtmlIntact -Label $c.Name -Html $c.Html
    $ok = ($r.Status -eq $c.Expect)
    if (-not $ok) { $failed++ }
    $mark = "PASS"; $col = "Green"
    if (-not $ok) { $mark = "BROKEN"; $col = "Red" }
    Write-Host ("  {0,-30} expected {1,-4} got {2,-4} " -f $c.Name, $c.Expect, $r.Status) -NoNewline
    Write-Host $mark -ForegroundColor $col
    if ($r.Status -ne "OK") { Write-Host ("      reason: " + $r.Detail) -ForegroundColor DarkGray }
  }
  Write-Host ""
  if ($failed -eq 0) {
    Write-Host "  Self-test passed: the asset check can both pass and fail." -ForegroundColor Green
    exit 0
  }
  Write-Host "  Self-test BROKEN: $failed case(s) did not behave as specified." -ForegroundColor Red
  exit 1
}

function Write-Row([string]$Name, [string]$Where, [string]$Status, [string]$Detail) {
  $color = "Red"
  if ($Status -eq "UP" -or $Status -eq "OK") { $color = "Green" }
  elseif ($Status -eq "SICK") { $color = "Yellow" }
  Write-Host ("  {0,-16} {1,-9} " -f $Name, $Where) -NoNewline
  Write-Host ("{0,-6}" -f $Status) -ForegroundColor $color -NoNewline
  Write-Host " $Detail"
}

Write-Host ""
Write-Host "======== Netie stack status ========" -ForegroundColor Cyan

$services = @(
  (Get-ServiceState -Name "Cortex engine" -Port $CortexPort     -Path "/health")
  (Get-ServiceState -Name "OpenVault API" -Port $OpenVaultPort  -Path "/api/healthz")
  (Get-ServiceState -Name "OpenVault UI"  -Port $OpenVaultUiPort -Path "/")
  (Get-ServiceState -Name "AirGPT"        -Port $AirGptPort     -Path "/api/info")
)

foreach ($s in $services) {
  $detail = $s.Detail
  if ($s.Status -eq "UP") { $detail = "$($s.Detail)  $($s.Ms)ms" }
  Write-Row $s.Name ":$($s.Port)" $s.Status $detail
}

# Netie Space is a desktop app - no port to probe, and Start-NetieStack.ps1 has
# -SkipNetieUi, so it is genuinely optional. Report it as OFF rather than DOWN and
# keep it out of the verdict: printing "DOWN" and then "all up" is the exact kind of
# contradictory green this script exists to kill.
$ns = @(Get-Process -Name "NetieSpace" -ErrorAction SilentlyContinue)
$netieSpaceUp = $ns.Count -gt 0
if ($netieSpaceUp) {
  Write-Row "Netie Space" "desktop" "UP" ("pid " + ($ns.Id -join ","))
} else {
  Write-Row "Netie Space" "desktop" "OFF" "not running (optional desktop UI, not counted below)"
}

# --- asset checks: what the browser actually receives ---
Write-Host ""
Write-Host "  Asset checks (served over HTTP, not read from disk)" -ForegroundColor Cyan

$assetChecks = @()
$airgpt = $services | Where-Object { $_.Name -eq "AirGPT" }
if ($airgpt.Status -ne "UP") {
  $assetChecks += [pscustomobject]@{
    Label = "AirGPT index.html"; Status = "SKIP"
    Detail = "AirGPT is not up - cannot check the page it serves"
  }
} else {
  $page = $null
  try {
    $page = (Invoke-WebRequest -Uri "http://127.0.0.1:$AirGptPort/" -UseBasicParsing -TimeoutSec 10).Content
  } catch {
    $page = $null
  }
  if ($null -eq $page) {
    $assetChecks += [pscustomobject]@{
      Label = "AirGPT index.html"; Status = "FAIL"; Detail = "could not fetch / from :$AirGptPort"
    }
  } else {
    $assetChecks += (Test-HtmlIntact -Label "AirGPT index.html" -Html $page)
    if ($Deep) {
      $assetChecks += (Test-InlineScriptSyntax -Label "AirGPT inline JS" -Html $page)
    }
  }
}

foreach ($a in $assetChecks) {
  $where = ""
  if ($a.Status -eq "SKIP") { $where = "skipped" }
  Write-Row $a.Label $where $a.Status $a.Detail
}

# --- verdict ---
$down = @($services | Where-Object { $_.Status -ne "UP" })
# SKIP counts as not-passing: an unrun check is not a green check.
$badAssets = @($assetChecks | Where-Object { $_.Status -ne "OK" })

Write-Host ""
$stackScript = Join-Path $OpenVaultRoot "scripts\windows\Start-NetieStack.ps1"

if ($down.Count -eq 0 -and $badAssets.Count -eq 0) {
  Write-Host ("  All {0} HTTP services up, all asset checks pass." -f $services.Count) -ForegroundColor Green
  if (-not $netieSpaceUp) {
    Write-Host "  Netie Space desktop UI is not running - optional, so not counted."
  }
  if (-not $Deep) { Write-Host "  Add -Deep to also syntax-check the served JavaScript." }
  Write-Host ""
  exit 0
}

if ($down.Count -gt 0) {
  Write-Host ("  {0} of {1} HTTP services not up: {2}" -f $down.Count, $services.Count, (($down | ForEach-Object { $_.Name }) -join ", ")) -ForegroundColor Yellow
  Write-Host "  Start everything:"
  Write-Host ("    powershell -ExecutionPolicy Bypass -File `"$stackScript`" -WithOpenVaultApp")
}
if ($badAssets.Count -gt 0) {
  Write-Host ("  {0} asset check(s) not passing:" -f $badAssets.Count) -ForegroundColor Yellow
  foreach ($a in $badAssets) { Write-Host ("    {0}: {1}" -f $a.Label, $a.Detail) }
  Write-Host "  A truncated page serves HTTP 200 while the app is dead. Restore it with:"
  Write-Host "    git -C D:\AirGPT checkout -- index.html"
}
Write-Host ""
exit 1
