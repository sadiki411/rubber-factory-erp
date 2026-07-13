$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = 'D:\develop\venvs\erp\Scripts\python.exe'
$nodeDir = 'D:\develop\node22'
$npm = Join-Path $nodeDir 'npm.cmd'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python virtual environment is missing. Run scripts\setup-dev.ps1 first.'
}
if (-not (Test-Path -LiteralPath $npm)) {
    throw 'Node.js is missing. Run scripts\setup-dev.ps1 first.'
}

$env:PATH = "$nodeDir;$env:PATH"
$env:PIP_CACHE_DIR = 'D:\develop\cache\pip'
$env:npm_config_cache = 'D:\develop\cache\npm'

Push-Location (Join-Path $root 'backend')
try {
    & $python manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    & $python manage.py init_erp
    if ($LASTEXITCODE -ne 0) { throw 'ERP initialization failed.' }
}
finally {
    Pop-Location
}

Write-Host 'Backend: http://127.0.0.1:8000'
Write-Host 'Frontend: http://127.0.0.1:5173'

$backend = Start-Process -FilePath $python -ArgumentList 'manage.py','runserver','127.0.0.1:8000' -WorkingDirectory (Join-Path $root 'backend') -PassThru -NoNewWindow
$frontend = Start-Process -FilePath $npm -ArgumentList 'run','dev','--','--host','127.0.0.1' -WorkingDirectory (Join-Path $root 'frontend') -PassThru -NoNewWindow

try {
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
        $backend.Refresh()
        $frontend.Refresh()
    }
    if ($backend.HasExited) {
        throw "Backend process exited with code $($backend.ExitCode)."
    }
    if ($frontend.HasExited) {
        throw "Frontend process exited with code $($frontend.ExitCode)."
    }
}
finally {
    Stop-Process -Id $backend.Id,$frontend.Id -Force -ErrorAction SilentlyContinue
}
