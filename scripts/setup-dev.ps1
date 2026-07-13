$ErrorActionPreference = 'Stop'

$developRoot = 'D:\develop'
$cacheRoot = Join-Path $developRoot 'cache'
$pythonRoot = Join-Path $developRoot 'python311'
$nodeRoot = Join-Path $developRoot 'node22'
$gitRoot = Join-Path $developRoot 'git'
$venvRoot = Join-Path $developRoot 'venvs\erp'
$projectRoot = Split-Path -Parent $PSScriptRoot
$curl = 'C:\Windows\System32\curl.exe'

if (-not (Test-Path -LiteralPath $curl)) {
    throw 'Windows curl.exe is required to download development tools.'
}

New-Item -ItemType Directory -Force -Path $developRoot,$cacheRoot,(Join-Path $developRoot 'venvs') | Out-Null

function Download-File([string]$Uri, [string]$Output) {
    & $curl -fL --retry 5 --retry-delay 2 --connect-timeout 30 -o $Output $Uri
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Uri" }
}

function Remove-DevelopDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $root = (Resolve-Path -LiteralPath $developRoot).Path
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not $resolved.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove a directory outside D:\develop: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Test-Python311([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $version = & $Path --version 2>&1
        return $LASTEXITCODE -eq 0 -and "$version" -match '^Python 3\.11\.'
    }
    catch { return $false }
}

function Test-Node22([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $version = & $Path --version 2>&1
        return $LASTEXITCODE -eq 0 -and "$version" -match '^v22\.'
    }
    catch { return $false }
}

function Test-Git([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        & $Path --version *> $null
        return $LASTEXITCODE -eq 0
    }
    catch { return $false }
}

$pythonExecutable = Join-Path $pythonRoot 'python.exe'
if (-not (Test-Python311 $pythonExecutable)) {
    Remove-DevelopDirectory $pythonRoot
    Write-Host 'Installing Python 3.11.9 into D:\develop\python311 ...'
    $pythonInstaller = Join-Path $cacheRoot 'python-3.11.9-amd64.exe'
    if (-not (Test-Path -LiteralPath $pythonInstaller) -or (Get-Item $pythonInstaller).Length -lt 20000000) {
        Remove-Item -LiteralPath $pythonInstaller -Force -ErrorAction SilentlyContinue
        Download-File 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' $pythonInstaller
    }
    $process = Start-Process -FilePath $pythonInstaller -ArgumentList '/quiet',"InstallAllUsers=0","TargetDir=$pythonRoot",'Include_pip=1','Include_test=0','Include_launcher=0','PrependPath=0','Shortcuts=0' -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Python installation failed with exit code $($process.ExitCode)." }
    if (-not (Test-Python311 $pythonExecutable)) { throw 'Python version validation failed after installation.' }
}

$nodeExecutable = Join-Path $nodeRoot 'node.exe'
if (-not (Test-Node22 $nodeExecutable)) {
    Remove-DevelopDirectory $nodeRoot
    Write-Host 'Installing the latest Node.js 22 LTS into D:\develop\node22 ...'
    $nodeIndexFile = Join-Path $cacheRoot 'node-index.json'
    Download-File 'https://nodejs.org/dist/index.json' $nodeIndexFile
    $nodeIndex = Get-Content -LiteralPath $nodeIndexFile -Raw | ConvertFrom-Json
    $release = $nodeIndex | Where-Object { $_.version -like 'v22.*' -and $_.lts } | Select-Object -First 1
    if (-not $release) { throw 'No Node.js 22 LTS release was found.' }
    $version = $release.version
    $nodeZip = Join-Path $cacheRoot "node-$version-win-x64.zip"
    if (-not (Test-Path -LiteralPath $nodeZip)) {
        Download-File "https://nodejs.org/dist/$version/node-$version-win-x64.zip" $nodeZip
    }
    $extractRoot = Join-Path $cacheRoot 'node-extract'
    Remove-DevelopDirectory $extractRoot
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Expand-Archive -LiteralPath $nodeZip -DestinationPath $extractRoot -Force
    Move-Item -LiteralPath (Join-Path $extractRoot "node-$version-win-x64") -Destination $nodeRoot
    if (-not (Test-Node22 $nodeExecutable)) { throw 'Node.js version validation failed after installation.' }
}

$gitExecutable = Join-Path $gitRoot 'cmd\git.exe'
if (-not (Test-Git $gitExecutable)) {
    Write-Host 'Installing PortableGit into D:\develop\git ...'
    $releaseFile = Join-Path $cacheRoot 'git-release.json'
    Download-File 'https://api.github.com/repos/git-for-windows/git/releases/latest' $releaseFile
    $release = Get-Content -LiteralPath $releaseFile -Raw | ConvertFrom-Json
    $asset = $release.assets | Where-Object { $_.name -like 'PortableGit-*-64-bit.7z.exe' } | Select-Object -First 1
    if (-not $asset) { throw 'No 64-bit PortableGit package was found.' }
    $gitInstaller = Join-Path $cacheRoot $asset.name
    if (-not (Test-Path -LiteralPath $gitInstaller)) {
        Download-File $asset.browser_download_url $gitInstaller
    }
    Remove-DevelopDirectory $gitRoot
    New-Item -ItemType Directory -Force -Path $gitRoot | Out-Null
    $process = Start-Process -FilePath $gitInstaller -ArgumentList '-y',"-o$gitRoot" -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "PortableGit installation failed with exit code $($process.ExitCode)." }
    if (-not (Test-Git $gitExecutable)) { throw 'Git validation failed after installation.' }
}

$python = Join-Path $pythonRoot 'python.exe'
$npm = Join-Path $nodeRoot 'npm.cmd'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

if (-not (Test-Python311 $venvPython)) {
    Remove-DevelopDirectory $venvRoot
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Python311 $venvPython)) {
        throw 'Python virtual environment creation failed.'
    }
}

$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:npm_config_cache = Join-Path $cacheRoot 'npm'
$env:PATH = "$nodeRoot;$gitRoot\cmd;$env:PATH"

& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'Python packaging tools update failed.' }
& $venvPython -m pip install -r (Join-Path $projectRoot 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
& $npm --prefix (Join-Path $projectRoot 'frontend') ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }

Write-Host "Python: $(& $python --version)"
Write-Host "Node.js: $(& $nodeExecutable --version)"
Write-Host "Git: $(& $gitExecutable --version)"
Write-Host 'Development tools and project dependencies are ready. This script does not inspect, install, or start Docker.'
