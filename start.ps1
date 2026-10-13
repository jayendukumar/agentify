[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$OpenBrowser,
    # Run against a separate demo database instead of the dev one. Run the
    # script again without -Demo to switch back.
    [switch]$Demo,
    # With -Demo: drop and recreate the demo database first.
    [switch]$ResetDemo,
    # With -Demo: seed the Epics 27-35 demo story once the API is up.
    [switch]$Seed
)

$ErrorActionPreference = 'Stop'

# Always run Compose from the repository root, even when this script is called
# using an absolute path from another directory.
Set-Location -LiteralPath $PSScriptRoot

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. Install Docker Desktop and make sure it is on PATH."
    }
}

Require-Command 'docker'

if (-not (Test-Path -LiteralPath 'docker-compose.yml')) {
    throw "docker-compose.yml was not found in $PSScriptRoot."
}

if (-not (Test-Path -LiteralPath 'backend\.env')) {
    throw "backend\.env is missing. Copy backend\.env.example to backend\.env and add your OPENROUTER_API_KEY."
}

if (($ResetDemo -or $Seed) -and -not $Demo) {
    throw '-ResetDemo and -Seed only apply together with -Demo.'
}

$demoDb = 'agentic_solution_generator_demo'
if ($Demo) {
    $env:APP_DB_NAME = $demoDb
    $env:JOB_CONCURRENCY = '8'
}
else {
    Remove-Item Env:APP_DB_NAME, Env:JOB_CONCURRENCY -ErrorAction SilentlyContinue
}

Write-Host 'Starting Agentic Solution Generator...' -ForegroundColor Cyan

if ($Demo) {
    & docker compose up -d --wait db
    if ($LASTEXITCODE -ne 0) {
        Write-Error "The database didn't start (exit code $LASTEXITCODE). Is Docker Desktop running?"
        exit 1
    }
    if ($ResetDemo) {
        Write-Host "Dropping the demo database ($demoDb)..." -ForegroundColor Yellow
        & docker compose exec -T db dropdb -U agentic --if-exists --force $demoDb
    }
    $exists = & docker compose exec -T db psql -U agentic -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$demoDb'"
    if ("$exists".Trim() -ne '1') {
        & docker compose exec -T db createdb -U agentic $demoDb
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Couldn't create the demo database $demoDb."
            exit 1
        }
        Write-Host "Created the demo database $demoDb."
    }
}

$composeArgs = @('compose', 'up', '-d')
if (-not $NoBuild) {
    $composeArgs += '--build'
}

try {
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed with exit code $LASTEXITCODE. Is Docker Desktop running?"
    }
}
catch {
    Write-Error $_
    exit 1
}

Write-Host ''
Write-Host 'Application started.' -ForegroundColor Green
if ($Demo) {
    Write-Host "Using the DEMO database ($demoDb). Run .\start.ps1 without -Demo to switch back." -ForegroundColor Yellow
}
Write-Host 'Frontend: http://localhost:3000'
Write-Host 'API:      http://localhost:8000'
Write-Host ''
Write-Host 'To stop it later, run: docker compose down'

if ($Seed) {
    # Migrations and the embedding model load before the API answers.
    Write-Host 'Waiting for the API...'
    $deadline = (Get-Date).AddMinutes(5)
    while ($true) {
        try {
            Invoke-RestMethod -Uri 'http://localhost:8000/healthz' -TimeoutSec 5 | Out-Null
            break
        }
        catch {
            if ((Get-Date) -gt $deadline) {
                Write-Error "The API didn't come up within 5 minutes -- check: docker compose logs api"
                exit 1
            }
            Start-Sleep -Seconds 3
        }
    }
    Require-Command 'python'
    & python (Join-Path $PSScriptRoot 'backend\scripts\demo_seed\seed_demo.py') --base-url 'http://localhost:8000'
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Seeding failed (exit code $LASTEXITCODE). Fix the cause, then re-run with -Demo -ResetDemo -Seed."
        exit 1
    }
}

if ($OpenBrowser) {
    Start-Process 'http://localhost:3000'
}
