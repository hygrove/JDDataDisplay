# JD Visualization - daily data refresh
# Called by Task Scheduler task JDViz_DailyRefresh at 03:00 daily.
# POST /api/refresh (rerun batch + clear cache), write result to logs/refresh.log.
$root = Split-Path $PSScriptRoot -Parent
$url = "http://127.0.0.1:8000/api/refresh"
$logDir = "$root\logs"
$logFile = "$logDir\refresh.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $resp = Invoke-WebRequest -Uri $url -Method Post -TimeoutSec 180 -UseBasicParsing
    $line = "$ts REFRESH OK $($resp.StatusCode) $($resp.Content)"
    [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
} catch {
    $line = "$ts REFRESH ERROR $($_.Exception.Message)"
    [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
}
