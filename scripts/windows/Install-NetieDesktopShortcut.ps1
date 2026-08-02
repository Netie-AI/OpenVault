#Requires -Version 5.1
<#
.SYNOPSIS
  Create a Desktop "Netie" shortcut with the Netie brand icon (sharp square mark).

.DESCRIPTION
  Netie (stack launcher) uses scripts\windows\assets\netie.ico — the square brand mark.
  Do NOT use Netie Space's rounded app icon (src\NetieSpace\Assets\netie.ico).
#>
param(
  [string]$OpenVaultRoot = "D:\OpenVault",
  [string]$NetieSpaceRoot = "D:\Space",
  [string]$Desktop = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"
$bat = Join-Path $OpenVaultRoot "scripts\windows\Start-Netie.bat"
$ico = Join-Path $OpenVaultRoot "scripts\windows\assets\netie.ico"
if (-not (Test-Path $bat)) { throw "Missing $bat" }
if (-not (Test-Path $ico)) {
  $builder = Join-Path $OpenVaultRoot "scripts\windows\assets\build_netie_icons.py"
  if (Test-Path $builder) {
    python $builder
  }
}
if (-not (Test-Path $ico)) {
  throw "Missing Netie brand icon: $ico (run assets\build_netie_icons.py)"
}

$lnkPath = Join-Path $Desktop "Netie.lnk"
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnkPath)
$s.TargetPath = $bat
$s.WorkingDirectory = Split-Path $bat -Parent
$s.WindowStyle = 1
$s.Description = "Netie - Cortex + OpenVault + AirGPT backend + Netie Space"
$s.IconLocation = "$ico,0"
$s.Save()

Write-Host "[OK] Desktop shortcut: $lnkPath" -ForegroundColor Green
Write-Host "     Icon: Netie brand (square) $ico" -ForegroundColor Gray
Write-Host "Double-click Netie to start the stack." -ForegroundColor Gray
