@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
  if errorlevel 1 (
    echo Setup failed. You can use the portable ZIP instead.
    pause
    exit /b 1
  )
)
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m worker_annotator %*
  exit /b 0
)
"%~dp0.venv\Scripts\python.exe" -m worker_annotator %*
if errorlevel 1 pause
