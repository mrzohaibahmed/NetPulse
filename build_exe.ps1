$ErrorActionPreference = "Stop"

# Use backend venv python automatically
$BackendVenvPython = Join-Path "backend\\venv\\Scripts" "python.exe"
if (!(Test-Path $BackendVenvPython)) {
  throw "backend venv not found. Expected: $BackendVenvPython"
}

# 1) Build frontend once (outputs frontend/dist)
Push-Location "frontend"
if (!(Test-Path "node_modules")) { npm install }
npm run build
Pop-Location

# 2) Build EXE (requires Python + pip)
& $BackendVenvPython -m pip install --upgrade pip
& $BackendVenvPython -m pip install pyinstaller

& $BackendVenvPython -m PyInstaller `
  --noconfirm `
  --onefile `
  --name "NetworkMonitor" `
  --paths "backend" `
  --hidden-import "app" `
  --add-data "backend;backend" `
  --add-data "frontend\\dist;frontend\\dist" `
  "run_netpulse.py"

Write-Host ""
Write-Host "EXE created at: dist\\NetworkMonitor.exe"

