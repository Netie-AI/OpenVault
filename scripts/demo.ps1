# nvme-sentinel one-click Windows demo
# Usage: .\scripts\demo.ps1  [-Device \\.\PhysicalDrive0]
param(
    [string]$Device = "",
    [switch]$OpenReport
)
$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   nvme-sentinel Windows Demo         ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Lint + typecheck (fast)
Write-Host "[1/4] Hygiene checks..." -ForegroundColor Yellow
uv run ruff check . --quiet
uv run mypy nvme_sentinel --no-error-summary | Select-String "error" | ForEach-Object { Write-Warning $_ }

# Tests
Write-Host "[2/4] Running test suite..." -ForegroundColor Yellow
uv run pytest tests/unit tests/integration -q --tb=short
if ($LASTEXITCODE -ne 0) { Write-Error "Tests failed. Aborting demo."; exit 1 }

# Demo command
Write-Host "[3/4] Running live demo..." -ForegroundColor Yellow
if ($Device) {
    uv run nvme-sentinel smart --device $Device --output reports/demo.html
} else {
    uv run nvme-sentinel demo
}

# Open report
Write-Host "[4/4] Opening report..." -ForegroundColor Yellow
$reportPath = (Resolve-Path "reports/demo.html").Path
Write-Host "Report: $reportPath" -ForegroundColor Green
if ($OpenReport) { Start-Process $reportPath }

Write-Host ""
Write-Host "Demo complete." -ForegroundColor Green
