#Requires -Version 5.1
<#
.SYNOPSIS
  Start OpenVault (+ optional Rust auth) and wire Cortex / FreeIDE URLs on Windows.

.DESCRIPTION
  Expects repo at D:\OpenVault (or -Root). Starts Python OpenVault console on :5000,
  optionally Rust auth console on :5055, then writes/approves the local connect pack.
  Pass -WithApp to also start the Next UI on :3010 and open /peers.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1 -WithApp
#>
param(
  [string]$Root = "D:\OpenVault",
  [string]$CortexUrl = "http://127.0.0.1:8000",
  [string]$OpenIdeUrl = "http://127.0.0.1:8765",
  [switch]$WithRustAuth,
  [switch]$WithApp,
  [switch]$SkipBrowser,
  [switch]$MockHealth
)

$ErrorActionPreference = "Stop"
Set-Location $Root

$env:OPENVAULT_HOME = Join-Path $Root ".openvault"
$env:CORTEX_URL = $CortexUrl
$env:OPENIDE_URL = $OpenIdeUrl
$env:OPENVAULT_RUST_URL = "http://127.0.0.1:5055"

Write-Host "==> Local mesh env" -ForegroundColor Cyan
Write-Host ("OPENVAULT_HOME=" + $env:OPENVAULT_HOME)
Write-Host ("CORTEX_URL=" + $CortexUrl)
Write-Host ("OPENIDE_URL=" + $OpenIdeUrl)

# Ensure uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv..." -ForegroundColor Yellow
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Set-Location (Join-Path $Root "OpenMW")
uv sync

$ovArgs = @(
  "run", "openmw", "console",
  "--host", "127.0.0.1",
  "--port", "5000",
  "--cortex-url", $CortexUrl,
  "--openide-url", $OpenIdeUrl
)
if ($MockHealth) { $ovArgs += "--mock-health" }
if ($SkipBrowser) { $ovArgs += "--no-open-browser" }

Write-Host "==> Starting OpenVault Python console :5000" -ForegroundColor Cyan
Start-Process -FilePath "uv" -ArgumentList $ovArgs -WorkingDirectory (Join-Path $Root "OpenMW")

if ($WithRustAuth) {
  if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Write-Host "==> Starting Rust auth console :5055" -ForegroundColor Cyan
    $rustDir = Join-Path $Root "OpenMW\rust\openvault-console"
    Start-Process -FilePath "cargo" -ArgumentList @("run", "--release") -WorkingDirectory $rustDir
  } else {
    Write-Host "cargo not found - skip Rust console" -ForegroundColor Yellow
  }
}

Write-Host "==> Waiting for OpenVault health..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/healthz" -TimeoutSec 2
    if ($h.status -eq "ok") { $ok = $true; break }
  } catch {
    Start-Sleep -Seconds 1
  }
  Start-Sleep -Seconds 1
}
if (-not $ok) { throw "OpenVault did not become healthy on :5000" }

Write-Host "==> Approving Cortex + FreeIDE on mesh" -ForegroundColor Cyan
Invoke-RestMethod -Method PUT -Uri "http://127.0.0.1:5000/api/local/mesh/config" -ContentType "application/json" -Body (@{
  auto_approve_loopback = $true
  cortex_url = $CortexUrl
  openide_url = $OpenIdeUrl
  rust_console_url = "http://127.0.0.1:5055"
} | ConvertTo-Json) | Out-Null

Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:5000/api/local/handshake" -ContentType "application/json" -Body (@{
  peer_kind = "cortex"
  name = "Cortex Netie Engine"
  base_url = $CortexUrl
  capabilities = @("engines", "models", "deploy")
  auto_approve = $true
} | ConvertTo-Json) | Out-Null

Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:5000/api/local/handshake" -ContentType "application/json" -Body (@{
  peer_kind = "openide"
  name = "FreeIDE local"
  base_url = $OpenIdeUrl
  capabilities = @("signin", "passkey", "editor")
  auto_approve = $true
} | ConvertTo-Json) | Out-Null

$pack = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/local/connect-pack"
$packPath = Join-Path $env:OPENVAULT_HOME "connect_pack.json"
$pack | ConvertTo-Json -Depth 8 | Set-Content -Path $packPath -Encoding UTF8

$ovBase = [string]$pack.openvault.base_url
$ideAnnounce = [string]$pack.openide.announce
$perfectMsg = [string]$pack.perfect_local.message

# Optional Next app (:3010) — custody UI lives at /peers for mesh
if ($WithApp) {
  $webDir = Join-Path $Root "apps\web"
  Write-Host "==> Starting OpenVault app :3010 (-WithApp)" -ForegroundColor Cyan
  if (-not (Test-Path (Join-Path $webDir "node_modules\next"))) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
      Push-Location $webDir
      npm install --no-audit --no-fund
      Pop-Location
    }
  }
  $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if (-not $npmCmd) { $npmCmd = Get-Command npm -ErrorAction SilentlyContinue }
  if ($npmCmd) {
    $env:PORT = "3010"
    $env:OPENVAULT_API_URL = "http://127.0.0.1:5000"
    Start-Process -FilePath $npmCmd.Source -ArgumentList @("run", "dev") -WorkingDirectory $webDir
    $appOk = $false
    for ($i = 0; $i -lt 60; $i++) {
      try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:3010/" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $appOk = $true; break }
      } catch { Start-Sleep -Seconds 1 }
      Start-Sleep -Seconds 1
    }
    if ($appOk -and -not $SkipBrowser) {
      Start-Process "http://127.0.0.1:3010/peers"
    } elseif (-not $appOk) {
      Write-Host "WARN: OpenVault app did not become ready on :3010" -ForegroundColor Yellow
    }
  } else {
    Write-Host "npm not found - skip -WithApp (use: python apps\cli\openvault_cli.py up)" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "OpenVault UI:     http://127.0.0.1:3010/peers  (start with -WithApp, or: python apps\cli\openvault_cli.py up)" -ForegroundColor Green
Write-Host "OpenVault API:    http://127.0.0.1:5000/  (redirects to app when :3010 is up)" -ForegroundColor Green
Write-Host ("Connect pack:     " + $packPath)
Write-Host ("Cortex should use OPENVAULT_URL=" + $ovBase)
Write-Host ("FreeIDE should POST handshake to " + $ideAnnounce)
Write-Host ("Perfect local:    " + $perfectMsg)
