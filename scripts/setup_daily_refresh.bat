@echo off
REM One-click installer for the daily 20:15 data refresh task (JDViz_DailyRefresh).
REM Idempotent: repeated double-clicks are safe (see _setup_daily_refresh.ps1).
REM Double-click -> UAC prompt -> registers the task. Then logs go to logs\refresh.log.
REM NOTE: keep this file pure ASCII (cmd reads it with the console codepage).
if "%~1"=="elevated" goto :run
powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
goto :eof
:run
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0_setup_daily_refresh.ps1"
echo.
echo Done. See logs\task_setup.log for details.
pause
