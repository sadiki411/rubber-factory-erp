$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pythonCandidates = @(
    'D:\develop\venvs\erp\Scripts\python.exe',
    'D:\develop\python311\python.exe'
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    throw 'Python 3.11 is missing. Run scripts\setup-dev.ps1 first.'
}

$composePath = Join-Path $root 'compose.yaml'
$workflowPath = Join-Path $root '.github\workflows\container-images.yml'
$dockerIgnorePath = Join-Path $root '.dockerignore'
$requiredFiles = @(
    $composePath,
    $workflowPath,
    $dockerIgnorePath,
    (Join-Path $root 'deploy\backend.Dockerfile'),
    (Join-Path $root 'deploy\web.Dockerfile'),
    (Join-Path $root 'deploy\backend-entrypoint.sh'),
    (Join-Path $root 'deploy\backup-loop.sh'),
    (Join-Path $root 'deploy\nginx.conf'),
    (Join-Path $root 'backend\requirements.txt'),
    (Join-Path $root 'frontend\package-lock.json')
)
foreach ($file in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Required deployment file is missing: $file" }
}

$env:COMPOSE_FILE_TO_CHECK = $composePath
@'
import os
from pathlib import Path
import yaml

path = Path(os.environ["COMPOSE_FILE_TO_CHECK"])
with path.open("r", encoding="utf-8") as stream:
    data = yaml.safe_load(stream)
if not isinstance(data, dict):
    raise SystemExit("compose.yaml must contain a mapping at the top level")
services = data.get("services") or {}
required = {"backend", "web", "backup"}
missing = sorted(required - set(services))
if missing:
    raise SystemExit(f"compose.yaml is missing services: {', '.join(missing)}")
backend = services["backend"]
if backend.get("deploy", {}).get("replicas", 1) != 1:
    raise SystemExit("SQLite deployment must use exactly one backend replica")
if not any("/app/data" in str(value) for value in backend.get("volumes", [])):
    raise SystemExit("backend is missing the SQLite persistent directory")
backup_env = services["backup"].get("environment", {})
if "BACKUP_RETENTION_COUNT" not in backup_env:
    raise SystemExit("backup is missing count-based retention")

for name in required:
    service = services.get(name) or {}
    image = str(service.get("image", ""))
    if name in {"backend", "backup"} and "GHCR_BACKEND_IMAGE" not in image:
        raise SystemExit(f"compose.yaml service {name} must use GHCR_BACKEND_IMAGE")
    if name == "web" and "GHCR_WEB_IMAGE" not in image:
        raise SystemExit("compose.yaml service web must use GHCR_WEB_IMAGE")
    if service.get("pull_policy") != "always":
        raise SystemExit(f"compose.yaml service {name} must always pull its image")
    if "build" in service:
        raise SystemExit(f"compose.yaml service {name} must not build from source")
print("compose.yaml parsed successfully; all services pull GHCR images directly.")
'@ | & $python -
if ($LASTEXITCODE -ne 0) { throw 'compose.yaml static validation failed.' }

$backendDockerfile = Get-Content -Raw -LiteralPath (Join-Path $root 'deploy\backend.Dockerfile')
$webDockerfile = Get-Content -Raw -LiteralPath (Join-Path $root 'deploy\web.Dockerfile')
if ($backendDockerfile -notmatch 'FROM python:3\.11-slim') {
    throw 'backend.Dockerfile must use a Python 3.11 slim base image.'
}
if ($backendDockerfile -match 'COPY\s+backend/\s') {
    throw 'backend.Dockerfile must not copy local databases or media with a broad backend COPY.'
}
foreach ($requiredCopy in @(
    'COPY backend/production/*.py /app/backend/production/',
    'COPY backend/production/migrations/*.py /app/backend/production/migrations/',
    'COPY backend/quality/*.py /app/backend/quality/',
    'COPY backend/quality/migrations/*.py /app/backend/quality/migrations/'
)) {
    if ($backendDockerfile -notmatch [regex]::Escape($requiredCopy)) {
        throw "backend.Dockerfile is missing required Django application source: $requiredCopy"
    }
}
if ($webDockerfile -match 'COPY\s+frontend/\s+\./') {
    throw 'web.Dockerfile must not copy host node_modules with a broad frontend COPY.'
}
$requirements = Get-Content -Raw -LiteralPath (Join-Path $root 'backend\requirements.txt')
if ($requirements -notmatch '(?m)^gunicorn==') {
    throw 'backend requirements must pin Gunicorn.'
}
Write-Host 'Dockerfile source and runtime version checks passed.'

$workflow = Get-Content -Raw -LiteralPath $workflowPath
foreach ($requiredText in @(
    'packages: write',
    'docker/build-push-action',
    'deploy/backend.Dockerfile',
    'deploy/web.Dockerfile',
    'linux/amd64,linux/arm64',
    'ghcr.io'
)) {
    if ($workflow -notmatch [regex]::Escape($requiredText)) {
        throw "GitHub Actions workflow is missing required content: $requiredText"
    }
}
$dockerIgnore = Get-Content -Raw -LiteralPath $dockerIgnorePath
foreach ($requiredPattern in @('/runtime/', '/*.xlsx', 'frontend/node_modules/', 'backend/data/')) {
    if ($dockerIgnore -notmatch [regex]::Escape($requiredPattern.TrimStart('/'))) {
        throw "Docker ignore file is missing required pattern: $requiredPattern"
    }
}
Write-Host 'GitHub Actions and Docker build-context checks passed.'

$gitSh = 'D:\develop\git\bin\sh.exe'
if (Test-Path -LiteralPath $gitSh) {
    & $gitSh -n (Join-Path $root 'deploy/backend-entrypoint.sh')
    if ($LASTEXITCODE -ne 0) { throw 'backend-entrypoint.sh syntax validation failed.' }
    & $gitSh -n (Join-Path $root 'deploy/backup-loop.sh')
    if ($LASTEXITCODE -ne 0) { throw 'backup-loop.sh syntax validation failed.' }
    Write-Host 'Shell entrypoint syntax validation passed.'
}
else {
    Write-Warning 'PortableGit sh.exe was not found; shell syntax validation was skipped.'
}

Write-Host 'Deployment files passed static checks. Docker was not installed, started, or invoked.'
