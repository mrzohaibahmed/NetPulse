@echo off
setlocal EnableExtensions
title NetPulse Stopper

echo ========================================
echo   NetPulse - Stopping services
echo ========================================
echo.

REM Close the launcher windows (kills child python/node too)
taskkill /FI "WINDOWTITLE eq NetPulse Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NetPulse Frontend*" /T /F >nul 2>&1

REM Fallback: free ports 5000 (API) and 5173 (UI)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = 5000,5173; foreach ($p in $ports) {" ^
  "  Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |" ^
  "    Select-Object -ExpandProperty OwningProcess -Unique |" ^
  "    ForEach-Object { if ($_ -and $_ -ne 0) {" ^
  "      Write-Host ('Stopping PID ' + $_ + ' on port ' + $p);" ^
  "      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue" ^
  "    }}" ^
  "}"

echo.
echo Done. Backend (:5000) and Frontend (:5173) should be stopped.
echo.
pause
endlocal
