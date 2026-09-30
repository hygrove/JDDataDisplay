# JD Visualization - daily data refresh (Windows launcher).
# Called by scheduled task JDViz_DailyRefresh at 20:15 daily.
#
# This file is a THIN launcher on purpose: it only resolves which python to use and
# then runs scripts/refresh_data.py, which holds all the real (cross-platform) logic.
# Resolving python at RUN TIME (instead of baking an absolute path into the scheduled
# task) is what makes the task survive moving the project to another machine, another
# drive, or rebuilding .venv elsewhere.
#
# NOTE: keep this file pure ASCII. PowerShell 5.1 reads .ps1 as ANSI/GBK, so UTF-8
# Chinese comments can corrupt parsing.

$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot "refresh_data.py"
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "refresh.log"

function Write-Log($m) {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $m
    [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
}

if (-not (Test-Path $script)) {
    Write-Log "LAUNCHER ERROR refresh_data.py not found at $script"
    exit 1
}

# Candidate interpreters, most specific first.
# Windows venv -> .venv\Scripts\python.exe ; Linux/Docker venv -> .venv/bin/python.
# The PATH fallback covers a globally installed python (e.g. inside a container).
$candidates = @(
    (Join-Path $root ".venv\Scripts\python.exe"),
    (Join-Path $root ".venv\bin\python.exe"),
    (Join-Path $root ".venv\bin\python"),
    (Join-Path $root "venv\Scripts\python.exe"),
    (Join-Path $root "venv\bin\python"),
    "python"
)

$py = $null
foreach ($c in $candidates) {
    if ($c -eq "python") {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source }
    } elseif (Test-Path $c) {
        $py = $c
    }
    if ($py) { break }
}

if (-not $py) {
    Write-Log "LAUNCHER ERROR no python found under $root (is .venv created? run: python -m venv .venv && pip install -r backend/requirements.txt)"
    exit 1
}

Write-Log "launcher python=$py"
& $py $script
$code = $LASTEXITCODE
Write-Log "launcher exit=$code"
exit $code
