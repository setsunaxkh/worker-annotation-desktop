param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Only Windows x64 is supported.' }
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$versionCheck = "import sys, struct, platform; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 and platform.machine().upper() in ('AMD64', 'X86_64'), 'Python 3.12 x64 required'"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $selectorArgs = @()
    if ($Python) { $pythonExe = $Python }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $pythonExe = 'py'; $selectorArgs = @('-3.12') }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $pythonExe = 'python' }
    else { throw 'Python 3.12 x64 was not found. Install it, or download the portable ZIP from Releases (no Python needed).' }
    & $pythonExe @selectorArgs -c $versionCheck
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required. Use the portable ZIP if Python is unavailable.' }
    & $pythonExe @selectorArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Use Python 3.12 x64 or the portable ZIP.' }
}
& $venvPython -c $versionCheck
if ($LASTEXITCODE -ne 0) { throw 'The virtual environment must use Python 3.12 x64.' }
& $venvPython -m pip install --require-hashes -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython -m pip install --no-deps --no-build-isolation --editable .
if ($LASTEXITCODE -ne 0) { throw 'Local source package installation failed.' }
& $venvPython scripts/fetch_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime download/verification failed.' }
Write-Host 'Ready. Double-click launch.cmd.'
