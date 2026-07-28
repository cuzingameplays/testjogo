@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".runtime\instalado-v2.ok" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALAR_ONLINE.ps1"
  if errorlevel 1 pause & exit /b 1
)
set "PATH=%~dp0.runtime\bin;%PATH%"
set "DUBSHOW_RUNTIME_DIR=%~dp0.runtime\salas"
start "" "http://127.0.0.1:8765"
"%~dp0.runtime\python\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8765 --workers 1
