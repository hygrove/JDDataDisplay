@echo off
REM JD viz local server launcher for Oray PeanutShell tunnel
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] venv not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)
echo Starting JD viz service on 0.0.0.0:8000 ...
start "JDViz" /min ".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --app-dir "%~dp0" --host 0.0.0.0 --port 8000
echo Done. Local: http://127.0.0.1:8000   Public (via PeanutShell): http://jdksh.top
echo Stop: kill the uvicorn process on port 8000 in Task Manager.
