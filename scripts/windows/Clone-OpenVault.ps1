#Requires -Version 5.1
<#
.SYNOPSIS
  Clone Netie-AI/OpenVault to D:\OpenVault and prepare the local mesh layout.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\Clone-OpenVault.ps1
#>
param(
  [string]$Target = "D:\OpenVault",
  [string]$RepoUrl = "https://github.com/Netie-AI/OpenVault.git",
  [string]$Branch = "cursor/openvault-local-mesh-aa83"
)

$ErrorActionPreference = "Stop"

Write-Host "==> OpenVault → $Target" -ForegroundColor Cyan

if (Test-Path (Join-Path $Target ".git")) {
  Write-Host "Repo already present. Fetching + checkout $Branch"
  Set-Location $Target
  git fetch origin
  git checkout $Branch
  git pull origin $Branch
} else {
  if (Test-Path $Target) {
    throw "Target $Target exists but is not a git repo. Move it aside or pass -Target."
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null
  git clone --branch $Branch $RepoUrl $Target
  Set-Location $Target
}

# Organised local data dirs (not committed)
$dirs = @(
  "D:\OpenVault\.openvault",
  "D:\OpenVault\.openvault\logs",
  "D:\OpenVault\.data\cortex",
  "D:\OpenVault\.data\openide"
)
foreach ($d in $dirs) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Copy-Item -Force "openvault.local.example.json" "openvault.local.json" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Clone ready." -ForegroundColor Green
Write-Host "Next:  powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1"
Write-Host "Docs:  README.md  STATUS.md  PARKINGLOT.md"
