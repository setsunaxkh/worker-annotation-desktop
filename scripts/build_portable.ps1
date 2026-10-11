param([string]$BuildDirectory = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run launch.cmd or scripts/bootstrap.cmd first.' }
& $venvPython scripts/fetch_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
& $venvPython scripts/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
$appDirectory = Join-Path $repoRoot 'dist\WorkerAnnotator'
$buildArgs = @()
if ($BuildDirectory) {
    $buildRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BuildDirectory))
    $cacheRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '.cache')) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $buildRoot.StartsWith($cacheRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'An isolated BuildDirectory must be inside this repository .cache directory.'
    }
    $distDirectory = Join-Path $buildRoot 'dist'
    $buildArgs = @('--distpath', $distDirectory, '--workpath', (Join-Path $buildRoot 'build'))
    $appDirectory = Join-Path $distDirectory 'WorkerAnnotator'
}
& $venvPython -m PyInstaller --noconfirm --clean @buildArgs worker-annotator.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
& $venvPython scripts/package_portable.py --app-dir $appDirectory
if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed.' }
