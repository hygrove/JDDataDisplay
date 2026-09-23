@echo off
REM Self-elevating installer for JD Visualization scheduled tasks.
REM Double-click -> UAC prompt -> registers JDViz_DailyRefresh + JDViz_UvicornAutoStart.
if "%~1"=="elevated" goto :run
powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
goto :eof
:run
powershell -ExecutionPolicy Bypass -File "%~dp0_install_tasks.ps1"
echo.
echo Install finished. See %~dp0_task.txt for details.
pause
