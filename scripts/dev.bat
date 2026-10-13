@echo off
REM One-shot dev launcher for JDDataDisplay.
REM Starts the backend (uvicorn :8000, --reload) in a detached window,
REM then runs the Vite dev server (:5173, HMR) in THIS window.
REM Open http://localhost:5173 for the live frontend;
REM its /api and /images requests are proxied to :8000 by Vite.
REM
REM Stop: Ctrl+C closes the Vite window; close the "JDViz-Backend" window
REM (or taskkill) to stop the backend.

cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtualenv not found: .venv\Scripts\python.exe
  echo Run:  python -m venv .venv
  echo       .venv\Scripts\python.exe -m pip install -r backend/requirements.txt
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo [WARN] frontend\node_modules missing. Run: cd frontend && npm install
)

start "JDViz-Backend" .venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

cd frontend
call npm run dev
