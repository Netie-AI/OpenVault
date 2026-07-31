@echo off
REM Netie — one-click stack (Cortex + OpenVault + AirGPT backend + Netie Space)
setlocal
cd /d "%~dp0..\.."
title Netie stack
echo.
echo  Starting Netie stack...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-NetieStack.ps1" %*
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" (
  echo  Stack failed with exit code %ERR%.
  echo  Press any key to close...
  pause >nul
  exit /b %ERR%
)
echo  Stack ready. Netie Space is in the system tray.
echo  Select a file in Explorer or Desktop, then press Space.
echo.
timeout /T 4 /NOBREAK >nul
exit /b 0
