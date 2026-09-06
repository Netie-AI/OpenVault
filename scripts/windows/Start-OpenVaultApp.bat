@echo off
REM OpenVault desktop app -- Electron + next dev so UI follows this repo.
cd /d "%~dp0..\.."
title OpenVault
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python apps\cli\openvault_cli.py app
  goto :done
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 apps\cli\openvault_cli.py app
  goto :done
)
echo python not found on PATH. Install Python 3.10+ and retry.
pause
exit /b 1
:done
if errorlevel 1 (
  echo OpenVault app exited with an error.
  pause
)
