#Requires -Version 5.1
<#
.SYNOPSIS
  Start OpenVault (+ optional Rust auth) and wire Cortex / OpenIDE URLs on Windows.

.DESCRIPTION
  Expects repo at D:\OpenVault (or -Root). Starts Python OpenVault console on :5000,
  optionally Rust auth console on :5055, then writes/approves the local connect pack.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1
#>
param(
  [string]$Root = "D:\OpenVault",
  [string]$CortexUrl = "http://127.0.0.1:8000",
  [string]$OpenIdeUrl = "http://127.0.0.1:5100",
  [switch]$WithRustAuth,
  [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
Set-Location $Root

$env:OPENVAULT_HOME = Join-Path $Root ".openvault"
$env:CORTEX_URL = $CortexUrl
$env:OPENIDE_URL = $OpenIdeUrl
$env:OPENVAULT_RUST_URL = "http://127.0.0.1:5055"

Write-Host "==> Local mesh env" -ForegroundColor Cyan
Write-Host "OPENVAULT_HOME=$env:OPENVAULT_HOME"
Write-Host "CORTEX_URL=$CortexUrl"
Write-Host "OPENIDE_URL=$OpenIdeUrl"

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
  "--openide-url", $OpenIdeUrl,
  "--mock-health"
)
if ($SkipBrowser) { $ovArgs += "--no-open-browser" }

Write-Host "==> Starting OpenVault Python console :5000" -ForegroundColor Cyan
Start-Process -FilePath "uv" -ArgumentList $ovArgs -WorkingDirectory (Join-Path $Root "OpenMW")

if ($WithRustAuth) {
  if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Write-Host "==> Starting Rust auth console :5055" -ForegroundColor Cyan
    $rustDir = Join-Path $Root "OpenMW\rust\openvault-console"
    Start-Process -FilePath "cargo" -ArgumentList @("run", "--release") -WorkingDirectory $rustDir
  } else {
    Write-Host "cargo not found — skip Rust console" -ForegroundColor Yellow
  }
}

Write-Host "==> Waiting for OpenVault health..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/healthz" -TimeoutSec 2
    if ($h.status -eq "ok") { $ok = $true; break }
  } catch { Start-Sleep -Seconds 1 }
  Start-Sleep -Seconds 1
}
if (-not $ok) { throw "OpenVault did not become healthy on :5000" }

Write-Host "==> Approving Cortex + OpenIDE on mesh" -ForegroundColor Cyan
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
  name = "OpenIDE local"
  base_url = $OpenIdeUrl
  capabilities = @("signin", "passkey", "editor")
  auto_approve = $true
} | ConvertTo-Json) | Out-Null

$pack = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/local/connect-pack"
$packPath = Join-Path $env:OPENVAULT_HOME "connect_pack.json"
$pack | ConvertTo-Json -Depth 8 | Set-Content -Path $packPath -Encoding UTF8

Write-Host ""
Write-Host "OpenVault UI:     http://127.0.0.1:5000/#mesh" -ForegroundColor Green
Write-Host "Connect pack:     $packPath"
Write-Host "Cortex should use OPENVAULT_URL=$($pack.openvault.base_url)"
Write-Host "OpenIDE should POST handshake to $($pack.openide.announce)"
Write-Host "Perfect local:    $($pack.perfect_local.message)"
