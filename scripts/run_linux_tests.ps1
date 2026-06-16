# Run the full test suite inside Docker from PowerShell on Windows.
# Requires: Docker Desktop running.
# Usage: .\scripts\run_linux_tests.ps1
param([switch]$Build, [switch]$RealDevice)

$ErrorActionPreference = "Stop"
Write-Host "==> nvme-sentinel Linux test runner (Docker)" -ForegroundColor Cyan

if ($Build) {
    Write-Host "Building image..." -ForegroundColor Yellow
    docker compose build test
}

if ($RealDevice) {
    Write-Host "Running with real NVMe device (requires Linux host + /dev/nvme0n1)" -ForegroundColor Yellow
    docker compose run --rm test-hw
} else {
    Write-Host "Running mock-adapter tests (no hardware needed)..." -ForegroundColor Yellow
    docker compose run --rm test
}

Write-Host "==> Done." -ForegroundColor Green
