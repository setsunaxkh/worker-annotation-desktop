@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run bootstrap.cmd once, or download and extract the portable ZIP from Releases.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
"%~dp0.venv\Scripts\python.exe" -m worker_annotator %*
if errorlevel 1 pause
