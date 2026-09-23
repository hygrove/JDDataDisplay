# Stop the JD Visualization backend service (process listening on port 8000).
# Called by stop_uvicorn.bat (double-click). Quick stop alternative to killing the process manually.
$conns = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "No process is listening on port 8000; nothing to stop."
    exit
}
$procIds = $conns.OwningProcess | Sort-Object -Unique
foreach ($procId in $procIds) {
    try {
        Stop-Process -Id $procId -Force
        Write-Host "Stopped PID $procId"
    } catch {
        $msg = $_.Exception.Message
        Write-Host "Failed to stop PID $procId : $msg"
    }
}
Write-Host "Done. To disable auto-start on next boot, disable JDViz_UvicornAutoStart in Task Scheduler."
