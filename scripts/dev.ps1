<#
  dev.ps1 - JDDataDisplay fullstack dev launcher
  Starts FastAPI backend (:8000) and Vite frontend (:5173).
  Already-running services are skipped (idempotent, safe to re-run).
  Usage:
    1) In VS Code integrated terminal:  ./scripts/dev.ps1
    2) Via the "JD Dev Server" terminal profile (see .vscode/settings.json)
#>

Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force

$root    = Resolve-Path (Join-Path $PSScriptRoot '..')
$backend = Join-Path $root '.venv\Scripts\python.exe'

function Test-PortListening($port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        return ($null -ne $conn)
    } catch {
        return $false
    }
}

Write-Host "`n=== JDDataDisplay dev server launcher ===`n" -ForegroundColor Magenta

# ---------- Backend (FastAPI / Uvicorn) ----------
if (Test-PortListening 8000) {
    Write-Host "[OK] Backend :8000 already running, skip." -ForegroundColor Green
} else {
    if (Test-Path $backend) {
        Write-Host "[>>] Starting backend (Uvicorn) -> http://127.0.0.1:8000 ..." -ForegroundColor Cyan
        $args = "-m uvicorn backend.app.main:app --app-dir `"$root`" --host 127.0.0.1 --port 8000"
        Start-Process -FilePath $backend -ArgumentList $args -NoNewWindow -WorkingDirectory $root
    } else {
        Write-Host "[X] venv not found at .venv. Create it first:`n   python -m venv .venv`n   .venv\Scripts\Activate.ps1`n   pip install -r requirements.txt" -ForegroundColor Red
    }
}

# ---------- Frontend (Vite) ----------
if (Test-PortListening 5173) {
    Write-Host "[OK] Frontend :5173 already running, skip." -ForegroundColor Green
} else {
    Write-Host "[>>] Starting frontend (Vite) -> http://localhost:5173 ..." -ForegroundColor Cyan
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" -NoNewWindow -WorkingDirectory (Join-Path $root 'frontend')
}

Write-Host "`n[WEB] Frontend:  http://localhost:5173" -ForegroundColor Yellow
Write-Host "[API] Backend:   http://127.0.0.1:8000" -ForegroundColor Yellow
Write-Host "[DOC] API docs:  http://127.0.0.1:8000/docs`n" -ForegroundColor Yellow
Write-Host "(Services run in background; closing this terminal stops them.)" -ForegroundColor Gray
Write-Host "(Stop backend via VS Code task 'Stop JD Visual Server'; stop frontend with Ctrl+C or close its window.)`n" -ForegroundColor Gray
