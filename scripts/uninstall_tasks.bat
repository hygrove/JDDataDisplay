@echo off
REM Self-elevating uninstaller for JD Visualization scheduled tasks.
REM Double-click -> UAC prompt -> removes JDViz_DailyRefresh + JDViz_UvicornAutoStart.
if "%~1"=="elevated" goto :run
powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
goto :eof
:run
powershell -Command "Unregister-ScheduledTask -TaskName 'JDViz_DailyRefresh' -Confirm:$false -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'JDViz_UvicornAutoStart' -Confirm:$false -ErrorAction SilentlyContinue; Write-Host 'Uninstalled JDViz tasks (if they existed).'"
echo.
pause
