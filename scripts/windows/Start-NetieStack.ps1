#Requires -Version 5.1
<#
.SYNOPSIS
  Start the full Netie stack: Cortex engine + OpenVault + AirGPT backend + Netie Space UI.

.DESCRIPTION
  One-click mesh for Netie Space as the front door.
  - Cortex API on :8010 (AirGPT cortex_client canonical)
  - OpenVault custody/gate/ship on :5000 (cortex_url pinned to :8010)
  - AirGPT on :8765 backend only (no browser - Netie Space is the chat UI)
  - Netie Space desktop app (preview + file-named chats)
  - Optional OpenVault Next UI on :3010 via -WithOpenVaultApp

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-NetieStack.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-NetieStack.ps1 -WithOpenVaultApp
#>
param(
  [string]$OpenVaultRoot = "D:\OpenVault",
  [string]$CortexRoot = "D:\Cortex",
  [string]$AirGptRoot = "D:\AirGPT",
  [string]$NetieSpaceRoot = "D:\Netie Space",
  [int]$CortexPort = 8010,
  [switch]$SkipNetieUi,
  [switch]$SkipAirGpt,
  [switch]$WithOpenVaultApp,
  [switch]$OpenBrowsers,
  [switch]$MockHealth
)

$ErrorActionPreference = "Stop"
$CortexUrl = "http://127.0.0.1:$CortexPort"
$OpenVaultUrl = "http://127.0.0.1:5000"
$AirGptUrl = "http://127.0.0.1:8765"

function Test-HttpOk([string]$Url, [string]$ExpectSubstring = "") {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
      if ($ExpectSubstring -and ($r.Content -notmatch [regex]::Escape($ExpectSubstring))) { return $false }
      return $true
    }
  } catch { }
  return $false
}

function Wait-HttpOk([string]$Url, [int]$Seconds = 60, [string]$Label = "service") {
  Write-Host "==> Waiting for $Label ($Url)..." -ForegroundColor Cyan
  for ($i = 0; $i -lt $Seconds; $i++) {
    if (Test-HttpOk $Url) {
      Write-Host "    $Label ready" -ForegroundColor Green
      return $true
    }
    Start-Sleep -Seconds 1
  }
  Write-Host "    WARN: $Label not ready after ${Seconds}s" -ForegroundColor Yellow
  return $false
}

Write-Host "======== Netie stack ========" -ForegroundColor Cyan
Write-Host "Cortex    $CortexUrl"
Write-Host "OpenVault $OpenVaultUrl"
Write-Host "AirGPT    $AirGptUrl (backend only)"
Write-Host "Netie UI  $NetieSpaceRoot"
Write-Host ""

# --- 1) Cortex engine :8010 ---
# Start uvicorn directly (avoid start_cortex_engine.ps1 StrictMode + stderr-as-error quitting).
if (-not (Test-HttpOk "$CortexUrl/health")) {
  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) { throw "python not found on PATH (needed for Cortex)" }
  $dataDir = Join-Path $CortexRoot "data\engine"
  New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
  Set-Content -Path (Join-Path $dataDir "cortex_api_url.txt") -Value $CortexUrl -Encoding utf8
  if (-not $env:DMS_AUTH_DISABLED -and -not $env:DMS_API_KEYS) {
    $env:DMS_API_KEYS = "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key"
  }
  $env:PACK = "dms"
  Write-Host "==> Starting Cortex engine :$CortexPort" -ForegroundColor Cyan
  Start-Process -FilePath "python" -ArgumentList @(
    "-m", "uvicorn", "CortexOS.api.main:app",
    "--host", "127.0.0.1", "--port", "$CortexPort"
  ) -WorkingDirectory $CortexRoot -WindowStyle Minimized
} else {
  Write-Host "==> Cortex already healthy" -ForegroundColor Green
}
[void](Wait-HttpOk "$CortexUrl/health" 90 "Cortex")

# --- 2) OpenVault :5000 ---
if (-not (Test-HttpOk "$OpenVaultUrl/api/healthz")) {
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  }
  $openMw = Join-Path $OpenVaultRoot "OpenMW"
  Write-Host "==> Starting OpenVault :5000 (cortex=$CortexUrl)" -ForegroundColor Cyan
  $env:OPENVAULT_HOME = Join-Path $OpenVaultRoot ".openvault"
  $env:CORTEX_URL = $CortexUrl
  $env:OPENIDE_URL = $AirGptUrl
  $env:CORTEX_API_URL = $CortexUrl
  $ovArgs = @(
    "run", "openmw", "console",
    "--host", "127.0.0.1", "--port", "5000",
    "--cortex-url", $CortexUrl,
    "--openide-url", $AirGptUrl,
    "--no-open-browser"
  )
  if ($MockHealth) { $ovArgs += "--mock-health" }
  Start-Process -FilePath "uv" -ArgumentList $ovArgs -WorkingDirectory $openMw -WindowStyle Minimized
} else {
  Write-Host "==> OpenVault already healthy" -ForegroundColor Green
}
[void](Wait-HttpOk "$OpenVaultUrl/api/healthz" 90 "OpenVault")

# Mesh handshake FreeIDE peer (Netie/AirGPT shell on :8765)
try {
  Invoke-RestMethod -Method Put -Uri "$OpenVaultUrl/api/local/mesh/config" -ContentType "application/json" -Body (@{
    auto_approve_loopback = $true
    cortex_url = $CortexUrl
    openide_url = $AirGptUrl
  } | ConvertTo-Json) | Out-Null
  Invoke-RestMethod -Method Post -Uri "$OpenVaultUrl/api/local/handshake" -ContentType "application/json" -Body (@{
    peer_kind = "openide"
    name = "Netie Space / AirGPT"
    base_url = $AirGptUrl
    capabilities = @("netie-space", "chat", "keyvault", "airgpt", "preview")
    auto_approve = $true
  } | ConvertTo-Json) | Out-Null
  Write-Host "==> Mesh handshake ok" -ForegroundColor Green
} catch {
  Write-Host ("==> Mesh handshake warn: " + $_.Exception.Message) -ForegroundColor Yellow
}

# --- 3) AirGPT backend only (no browser) ---
if (-not $SkipAirGpt) {
  if (-not (Test-HttpOk "$AirGptUrl/api/info")) {
    $clip = Join-Path $AirGptRoot "clipdrop.py"
    if (-not (Test-Path $clip)) { throw "Missing $clip" }
    Write-Host "==> Starting AirGPT backend :8765 (no UI browser)" -ForegroundColor Cyan
    $env:CORTEX_API_URL = $CortexUrl
    $env:OPENVAULT_URL = $OpenVaultUrl
    $env:AIRGPT_NO_BROWSER = "1"
    Start-Process -FilePath "python" -ArgumentList @($clip) -WorkingDirectory $AirGptRoot -WindowStyle Minimized
  } else {
    Write-Host "==> AirGPT already listening" -ForegroundColor Green
  }
  [void](Wait-HttpOk "$AirGptUrl/api/info" 90 "AirGPT")
}

# --- 4) Netie Space UI ---
if (-not $SkipNetieUi) {
  $exe = Join-Path $NetieSpaceRoot "dist\NetieSpace\NetieSpace.exe"
  $runBat = Join-Path $NetieSpaceRoot "run.bat"
  $env:OPENVAULT_URL = $OpenVaultUrl
  $env:OPENIDE_URL = $AirGptUrl
  $env:CORTEX_API_URL = $CortexUrl
  $env:AIRGPT_URL = $AirGptUrl
  $env:NETIE_OPENVAULT_OK = "1"
  $env:CORTEX_URL = $CortexUrl

  $existing = @(Get-Process -Name "NetieSpace" -ErrorAction SilentlyContinue)
  if ($existing.Count -gt 0) {
    Write-Host ("==> Netie Space already running (pid " + ($existing.Id -join ",") + ") — pinging tray") -ForegroundColor Green
    # Second-instance launch quietly pings the live tray (no MessageBox)
    if (Test-Path $exe) {
      Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent) -WindowStyle Hidden
    }
  } elseif (Test-Path $exe) {
    Write-Host "==> Launching Netie Space UI (OPENVAULT_URL=$OpenVaultUrl CORTEX=$CortexUrl)" -ForegroundColor Cyan
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent)
    Start-Sleep -Seconds 2
    if (Get-Process -Name "NetieSpace" -ErrorAction SilentlyContinue) {
      Write-Host "==> Netie Space process up" -ForegroundColor Green
    } else {
      Write-Host "==> WARN: NetieSpace.exe exited immediately — see %LOCALAPPDATA%\NetieSpace\logs\app.log" -ForegroundColor Yellow
    }
  } else {
    Write-Host "NetieSpace.exe not found at $exe - run Netie Space scripts\publish.ps1" -ForegroundColor Yellow
    if (Test-Path $runBat) {
      Start-Process -FilePath $runBat -WorkingDirectory $NetieSpaceRoot
    }
  }
}

if ($WithOpenVaultApp) {
  $webDir = Join-Path $OpenVaultRoot "apps\web"
  Write-Host "==> Starting OpenVault app :3010 (-WithOpenVaultApp)" -ForegroundColor Cyan
  if (-not (Test-HttpOk "http://127.0.0.1:3010/")) {
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
      $env:OPENVAULT_API_URL = $OpenVaultUrl
      Start-Process -FilePath $npmCmd.Source -ArgumentList @("run", "dev") -WorkingDirectory $webDir -WindowStyle Minimized
      [void](Wait-HttpOk "http://127.0.0.1:3010/" 90 "OpenVault app")
    } else {
      Write-Host "npm not found - skip OpenVault app" -ForegroundColor Yellow
    }
  } else {
    Write-Host "==> OpenVault app already listening on :3010" -ForegroundColor Green
  }
  if ($OpenBrowsers -or $WithOpenVaultApp) {
    Start-Process "http://127.0.0.1:3010/"
  }
}

if ($OpenBrowsers) {
  Start-Process $OpenVaultUrl
  if (-not $SkipAirGpt) { Start-Process $AirGptUrl }
}

Write-Host ""
Write-Host "======== Ready ========" -ForegroundColor Green
Write-Host "Netie Space = chat + files (front door)"
Write-Host "AirGPT      = backend keys/RAG/sync only ($AirGptUrl)"
Write-Host "OpenVault   = custody + gate ($OpenVaultUrl)"
Write-Host "OpenVault UI= http://127.0.0.1:3010/  (use -WithOpenVaultApp)"
Write-Host "Cortex      = brain ($CortexUrl)"
Write-Host "File chat titles = file name; Add to chat syncs AirGPT chatspace when online."
