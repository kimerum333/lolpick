param([string]$Python = '', [switch]$Native, [string]$Docker = '')
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
}
$taskNames = @('APP_ENV','PYTHONDONTWRITEBYTECODE','TEST_DATABASE_URL','TEST_ALLOW_SCHEMA_RESET')
$taskOld = @{}
foreach ($name in $taskNames) { $taskOld[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
$taskContainer = 'lolpick-runtime-' + [guid]::NewGuid().ToString('N')
$taskCreated = $false
Push-Location $PSScriptRoot
try {
    $env:APP_ENV = 'test'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:TEST_DATABASE_URL = $null
    $env:TEST_ALLOW_SCHEMA_RESET = $null
    if ($Native) {
        if (-not $Docker) {
            $taskDocker = Get-Command docker -ErrorAction SilentlyContinue
            if ($taskDocker) { $Docker = $taskDocker.Source }
            else {
                $taskCandidates = @(
                    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
                    (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')
                )
                $Docker = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            }
        }
        if (-not $Docker) { throw 'Docker CLI is unavailable. Native checks did not run.' }
        & $Docker info --format '{{.ServerVersion}}'
        if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable. Native checks did not run.' }
        $taskPassword = [guid]::NewGuid().ToString('N')
        & $Docker create --name $taskContainer -e "POSTGRES_PASSWORD=$taskPassword" -e 'POSTGRES_DB=lolpick_test' -p '127.0.0.1:55432:5432' postgres:18
        if ($LASTEXITCODE -ne 0) { throw 'Disposable local PostgreSQL creation failed.' }
        $taskCreated = $true
        & $Docker start $taskContainer
        if ($LASTEXITCODE -ne 0) { throw 'Disposable local PostgreSQL startup failed.' }
        $taskReady = $false
        for ($attempt=0; $attempt -lt 45; $attempt++) {
            & $Docker exec $taskContainer pg_isready -U postgres -d lolpick_test 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $taskReady=$true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $taskReady) { throw 'Disposable PostgreSQL did not become ready.' }
        $env:TEST_DATABASE_URL="postgresql+psycopg://postgres:${taskPassword}@127.0.0.1:55432/lolpick_test"
        $env:TEST_ALLOW_SCHEMA_RESET='1'
    }
    & $Python -B -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw 'Backend verification failed.' }
    if ($Native) {
        & $Python -B scripts/verify_postgres.py
        if ($LASTEXITCODE -ne 0) { throw 'Native v7 schema verification failed.' }
    }
    & $Python -B -m ruff check app migrations tests
    if ($LASTEXITCODE -ne 0) { throw 'Static verification failed.' }
}
finally {
    foreach ($name in $taskNames) { [Environment]::SetEnvironmentVariable($name,$taskOld[$name],'Process') }
    if ($taskCreated) { & $Docker rm -f $taskContainer | Out-Null }
    Pop-Location
}
