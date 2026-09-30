# JD Visualization - one-click installer for the daily 20:15 data refresh task.
# Creates/updates scheduled task JDViz_DailyRefresh. Must run elevated (the .bat wrapper
# triggers the UAC prompt).
#
# Idempotency (convergent, so repeated double-clicks are safe):
#   * task missing                      -> create it at 20:15
#   * task exists AND already at 20:15  -> do nothing (this is the "ignore" case)
#   * task exists but at a wrong time   -> bring it back to 20:15 (e.g. the old 03:00 task)
# After the first corrective run, every later click is a no-op.
#
# NOTE: keep this file pure ASCII. PowerShell 5.1 reads .ps1 as ANSI/GBK, so UTF-8
# Chinese comments can corrupt parsing.

$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "task_setup.log"
$taskName = "JDViz_DailyRefresh"
$ps1 = Join-Path $PSScriptRoot "refresh_daily.ps1"

function Write-Log($m) {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $m
    [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
}

Write-Log "===== setup start (task=$taskName) ====="

if (-not (Test-Path $ps1)) {
    Write-Log "SETUP ERROR refresh_daily.ps1 not found at $ps1"
    Write-Output "ERROR: refresh_daily.ps1 not found."
    exit 1
}

function Register-RefreshTask {
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" `
        -Argument "-ExecutionPolicy Bypass -NoProfile -File `"$ps1`"" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At "20:15"
    # SYSTEM: no password to store, and it runs even when nobody is logged in.
    # (Do not hardcode a username here - that would break on another machine.)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if (-not $existing) {
    try {
        Register-RefreshTask
        Write-Log "CREATED OK (daily 20:15, SYSTEM)"
        Write-Output "Created scheduled task '$taskName': runs daily at 20:15."
    } catch {
        Write-Log "CREATE ERROR: $_"
        Write-Output "Failed to create task: $_"
        exit 1
    }
} else {
    # Does any trigger already point at 20:15?
    $match = $false
    foreach ($t in $existing.Triggers) {
        if ($t.StartBoundary -like "*T20:15*") { $match = $true; break }
    }
    if ($match) {
        Write-Log "ALREADY OK (daily 20:15) -> skipped, no changes"
        Write-Output "Task '$taskName' already exists at 20:15. Nothing to do."
    } else {
        try {
            Register-RefreshTask
            Write-Log "UPDATED OK -> moved to daily 20:15"
            Write-Output "Task '$taskName' existed with a different schedule; updated to daily 20:15."
        } catch {
            Write-Log "UPDATE ERROR: $_"
            Write-Output "Failed to update task: $_"
            exit 1
        }
    }
}

Write-Log "===== setup end ====="
exit 0
