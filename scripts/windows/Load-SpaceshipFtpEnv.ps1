#Requires -Version 5.1
<#
.SYNOPSIS
  Map the existing Spaceship FTP cache into OpenVault ship env names.

.DESCRIPTION
  Landing uses FTP_HOST / FTP_USER / FTP_PASS in
  %USERPROFILE%\.netie\spaceship-ftp.env. OpenVault ship reads SPACESHIP_FTP_*.
  This loader copies names only. It never prints a password.

  Does NOT set OPENVAULT_SPACESHIP_ALLOW_PUBLISH. Pass -AllowPublish when you
  are ready to overwrite the live host (HT1). Default public URL is the
  ht1-demo subpath, not https://netie.ai/ (that would clobber the homepage).

.EXAMPLE
  . E:\OpenVault\scripts\windows\Load-SpaceshipFtpEnv.ps1
.EXAMPLE
  . E:\OpenVault\scripts\windows\Load-SpaceshipFtpEnv.ps1 -CreateTemplate
.EXAMPLE
  . E:\OpenVault\scripts\windows\Load-SpaceshipFtpEnv.ps1 -AllowPublish
#>
param(
  [switch]$CreateTemplate,
  [switch]$AllowPublish,
  [string]$EnvFile = (Join-Path $env:USERPROFILE ".netie\spaceship-ftp.env")
)

$ErrorActionPreference = "Stop"

function Set-IfEmpty([string]$Name, [string]$Value) {
  if (-not $Value) { return }
  $existing = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($existing)) {
    Set-Item -Path "Env:$Name" -Value $Value
  }
}

$dir = Split-Path $EnvFile -Parent
if (-not (Test-Path $dir)) {
  New-Item -ItemType Directory -Path $dir | Out-Null
}

if ($CreateTemplate -or -not (Test-Path $EnvFile)) {
  if (-not (Test-Path $EnvFile)) {
    @"
# Cache for this laptop only. Never git this file. Never paste FTP_PASS in chat.
# Values from Spaceship cPanel -> FTP Accounts (see Internal/Agents/SHIP_SPACESHIP.md).
# Host and user are blank on purpose: this repo is public. Fill all three locally.
FTP_HOST=
FTP_USER=
FTP_PASS=
# Optional OpenVault names (used if FTP_* aliases are also present):
# SPACESHIP_FTP_DIR=ht1-demo
# SPACESHIP_PUBLIC_URL=https://netie.ai/ht1-demo
# SPACESHIP_FTP_ENV_DIR=ov-env
"@ | Set-Content -Path $EnvFile -Encoding ascii
    Write-Host "Wrote template $EnvFile"
    Write-Host "Paste FTP_PASS from cPanel, then re-run this script (dot-source it)."
    Write-Host "cPanel FTP: https://www.spaceship.com/application/hosting-manager/"
    return
  }
}

Get-Content $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $eq = $line.IndexOf("=")
  if ($eq -lt 1) { return }
  $key = $line.Substring(0, $eq).Trim()
  $val = $line.Substring($eq + 1).Trim().Trim("'").Trim('"')
  if ($key -and $val) {
    Set-Item -Path "Env:$key" -Value $val
  }
}

Set-IfEmpty "SPACESHIP_FTP_HOST" $env:FTP_HOST
Set-IfEmpty "SPACESHIP_FTP_USER" $env:FTP_USER
Set-IfEmpty "SPACESHIP_FTP_PASS" $env:FTP_PASS
Set-IfEmpty "SPACESHIP_FTP_DIR" "ht1-demo"
Set-IfEmpty "SPACESHIP_PUBLIC_URL" "https://netie.ai/ht1-demo"
Set-IfEmpty "SPACESHIP_FTP_ENV_DIR" "ov-env"

if ($AllowPublish) {
  $env:OPENVAULT_SPACESHIP_ALLOW_PUBLISH = "1"
}

$passSet = -not [string]::IsNullOrWhiteSpace($env:SPACESHIP_FTP_PASS)
Write-Host "SPACESHIP_FTP_HOST=$($env:SPACESHIP_FTP_HOST)"
Write-Host "SPACESHIP_FTP_USER=$($env:SPACESHIP_FTP_USER)"
Write-Host "SPACESHIP_FTP_PASS set: $passSet"
Write-Host "SPACESHIP_FTP_DIR=$($env:SPACESHIP_FTP_DIR)"
Write-Host "SPACESHIP_PUBLIC_URL=$($env:SPACESHIP_PUBLIC_URL)"
Write-Host "SPACESHIP_FTP_ENV_DIR=$($env:SPACESHIP_FTP_ENV_DIR)"
Write-Host "OPENVAULT_SPACESHIP_ALLOW_PUBLISH=$($env:OPENVAULT_SPACESHIP_ALLOW_PUBLISH)"
if (-not $passSet) {
  Write-Host "STOP: FTP_PASS is empty. Open cPanel FTP Accounts, paste into $EnvFile, re-run."
}
if ($env:SPACESHIP_PUBLIC_URL -eq "https://netie.ai" -or $env:SPACESHIP_PUBLIC_URL -eq "https://netie.ai/") {
  Write-Host "WARN: public URL is the homepage. HT1 sample should use /ht1-demo/ so you do not clobber netie.ai."
}
