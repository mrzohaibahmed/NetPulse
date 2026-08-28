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

REM --- Frontend production build check ---
if not exist "frontend\dist\index.html" (
    echo [ERROR] Frontend production build not found.
    echo.
    echo Run:
    echo.
    echo     cd frontend
    echo     npm install
    echo     npm run build
    echo.
    echo Then run start.bat again.
    echo.
    pause
    exit /b 1
)

echo [1/1] Starting backend / production UI
echo       http://0.0.0.0:5000
echo.
start "NetPulse Backend" cmd /k "cd /d "%~dp0backend" && call venv\Scripts\activate.bat && python app.py"

REM Give Flask a moment to bind before opening the browser
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:5000"

echo.
echo Backend window: NetPulse Backend
echo.
echo Backend: http://127.0.0.1:5000
echo LAN UI:  http://^<HOST-LAN-IP^>:5000
echo.
echo Frontend:
echo React production build served by Flask
echo.
echo Development frontend:
echo Run "npm run dev" manually from frontend\
echo.
echo Close the backend window to stop NetPulse.
echo.
pause
endlocal
