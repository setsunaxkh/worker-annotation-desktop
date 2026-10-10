$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run bootstrap.cmd first.' }
& $venvPython scripts/fetch_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
& $venvPython scripts/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $venvPython -m PyInstaller --noconfirm --clean worker-annotator.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
& $venvPython scripts/package_portable.py
if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed.' }
