@echo off
powershell.exe -ExecutionPolicy Bypass -File "%~dp0stop_uvicorn.ps1"
pause
