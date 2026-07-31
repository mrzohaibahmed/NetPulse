@echo off
setlocal EnableExtensions
title NetPulse Launcher

cd /d "%~dp0"

echo ========================================
echo   NetPulse - Starting services
echo ========================================
echo.

REM --- Backend checks ---
if not exist "backend\venv\Scripts\python.exe" (
    echo [ERROR] Backend venv not found at backend\venv
    echo         Run:  cd backend ^& python -m venv venv ^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "backend\.env" (
    echo [WARN] backend\.env missing. Copy from backend\.env.example before use.
    echo.
)

REM --- Frontend checks ---
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm not found. Install Node.js 18+ and ensure it is on PATH.
    pause
    exit /b 1
)

if not exist "frontend\node_modules\" (
    echo [INFO] frontend\node_modules missing - running npm install...
    pushd frontend
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        popd
        pause
        exit /b 1
    )
    popd
    echo.
)

echo [1/2] Starting backend  (http://127.0.0.1:5000^)
start "NetPulse Backend" cmd /k "cd /d "%~dp0backend" && call venv\Scripts\activate.bat && python app.py"

REM Give Flask a moment before Vite proxies to it
timeout /t 2 /nobreak >nul

echo [2/2] Starting frontend (http://127.0.0.1:5173^)
start "NetPulse Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5173"

echo.
echo Backend  window: NetPulse Backend
echo Frontend window: NetPulse Frontend
echo UI:  http://127.0.0.1:5173
echo API: http://127.0.0.1:5000
echo.
echo Close those windows to stop the services.
echo.
pause
endlocal
