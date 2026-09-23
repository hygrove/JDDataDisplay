$root = Split-Path $PSScriptRoot -Parent
$log = Join-Path $PSScriptRoot "_task.txt"
function Log($m){ [System.IO.File]::AppendAllText($log, $m + [Environment]::NewLine, [System.Text.Encoding]::UTF8) }
Log("start")
try {
  $act1 = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument " -ExecutionPolicy Bypass -File `"$root\scripts\refresh_daily.ps1`""
  $trig1 = New-ScheduledTaskTrigger -Daily -At "03:00"
  $prin1 = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  $set1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName "JDViz_DailyRefresh" -Action $act1 -Trigger $trig1 -Principal $prin1 -Settings $set1 -Force -ErrorAction Stop
  Log("DailyRefresh registered OK")
} catch { Log("DailyRefresh ERR: $_") }
try {
  $act2 = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\python.exe" -Argument "-m uvicorn backend.app.main:app --app-dir $root --host 0.0.0.0 --port 8000" -WorkingDirectory $root
  $trig2 = New-ScheduledTaskTrigger -AtLogOn
  $prin2 = New-ScheduledTaskPrincipal -UserId "admin" -LogonType Interactive -RunLevel Highest
  $set2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName "JDViz_UvicornAutoStart" -Action $act2 -Trigger $trig2 -Principal $prin2 -Settings $set2 -Force -ErrorAction Stop
  Log("UvicornAutoStart registered OK")
} catch { Log("UvicornAutoStart ERR: $_") }
Log("done")
