#Requires -Version 5.1
<#
.SYNOPSIS
  Desktop + Start Menu shortcut that opens the OpenVault desktop app.

.DESCRIPTION
  Double-click OpenVault. Uses apps/shell Electron wrapping next dev, so the
  UI follows this repo. Not the Netie full-stack shortcut.
#>
param(
  [string]$OpenVaultRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$Desktop = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"
$bat = Join-Path $OpenVaultRoot "scripts\windows\Start-OpenVaultApp.bat"
$ico = Join-Path $OpenVaultRoot "apps\shell\electron\assets\icon.ico"
if (-not (Test-Path $bat)) { throw "Missing $bat" }
if (-not (Test-Path $ico)) {
  $ico = Join-Path $OpenVaultRoot "scripts\windows\assets\netie.ico"
}

function Write-Shortcut([string]$lnkPath) {
  $w = New-Object -ComObject WScript.Shell
  $s = $w.CreateShortcut($lnkPath)
  $s.TargetPath = $bat
  $s.WorkingDirectory = $OpenVaultRoot
  $s.WindowStyle = 7
  $s.Description = "OpenVault - local vault, keys, grant"
  if (Test-Path $ico) { $s.IconLocation = "$ico,0" }
  $s.Save()
}

$desktopLnk = Join-Path $Desktop "OpenVault.lnk"
Write-Shortcut $desktopLnk

$programs = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
$startLnk = Join-Path $programs "OpenVault.lnk"
Write-Shortcut $startLnk

Write-Host "[OK] Desktop: $desktopLnk"
Write-Host "[OK] Start Menu: $startLnk"
Write-Host "Double-click OpenVault. Code changes in this repo show after Next reloads."
