#Requires -Version 5.1
<#
.SYNOPSIS
  Full OpenVault demo — custody API (:5000) + Next app (:3010).

.DESCRIPTION
  Starts the real UI (apps/web), not the deleted OpenMW HTML console.
  Uses --mock-health so Detect always has demo device numbers.
  Opens http://127.0.0.1:3010/

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-OpenVaultDemo.ps1
#>
param(
  [string]$OpenVaultRoot = "D:\OpenVault",
  [switch]$Prod
)

$ErrorActionPreference = "Stop"
$cli = Join-Path $OpenVaultRoot "apps\cli\openvault_cli.py"
if (-not (Test-Path $cli)) { throw "Missing $cli" }

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not found on PATH" }

Write-Host "======== OpenVault demo ========" -ForegroundColor Cyan
Write-Host "API  http://127.0.0.1:5000/"
Write-Host "App  http://127.0.0.1:3010/  <- use this"
Write-Host ""

$args = @($cli, "demo")
if ($Prod) { $args += "--prod" }
& python @args
