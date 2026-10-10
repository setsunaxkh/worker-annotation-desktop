@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if errorlevel 1 (
  echo Setup failed. See the message above. You can use the portable release instead.
  pause
  exit /b 1
)
pause
