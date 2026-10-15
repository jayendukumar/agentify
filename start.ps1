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
    [switch]$Seed,
    # With -Demo: seed the Epic 36 "Invoice to Pay" process twin demo.
    [switch]$SeedProcessTwin,
    # Epic 37: sign in through a local Keycloak (OIDC + MFA) instead of the
    # name picker. See testing/keycloak/README.md. Run again without -Sso
    # to switch back (that also stops Keycloak).
    [switch]$Sso
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

if ($Sso -and ($Seed -or $SeedProcessTwin)) {
    # The seed scripts sign in with the local name picker, which SSO turns off.
    throw 'Seed without -Sso first, then restart with -Sso.'
}

if (($ResetDemo -or $Seed -or $SeedProcessTwin) -and -not $Demo) {
    throw '-ResetDemo, -Seed and -SeedProcessTwin only apply together with -Demo.'
}

$demoDb = 'agentic_solution_generator_demo'
if ($Demo) {
    $env:APP_DB_NAME = $demoDb
    $env:JOB_CONCURRENCY = '8'
    $env:APP_ENVIRONMENT = 'demo'
}
else {
    Remove-Item Env:APP_DB_NAME, Env:JOB_CONCURRENCY -ErrorAction SilentlyContinue
    $env:APP_ENVIRONMENT = 'development'
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

$composeFiles = @('-f', 'docker-compose.yml')
if ($Sso) {
    $composeFiles += @('-f', 'docker-compose.sso.yml')
}
$composeArgs = @('compose') + $composeFiles + @('up', '-d', '--remove-orphans')
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
if ($Sso) {
    Write-Host 'Keycloak: http://localhost:8180 (sign-in people and their one-time codes: testing\keycloak\README.md)'
}
Write-Host ''
Write-Host "To stop it later, run: docker compose $($composeFiles -join ' ') down"

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

# Epic 38: a second tenant, so isolation can be seen in a browser. Its data
# is entirely separate from Meridian's (the default tenant, at localhost).
& docker compose exec -T api python -m app.tenancy ensure northwind --name 'Northwind Traders' --host northwind.localhost | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Couldn't create the northwind tenant -- check: docker compose logs api"
}
# Epic 38, US38.6: with -Sso, Northwind signs in through its own Keycloak
# realm; without it, it goes back to local sign-in.
if ($Sso) {
    & docker compose exec -T api python -m app.tenancy identity set northwind `
        --issuer 'http://localhost:8180/realms/northwind' `
        --discovery-url 'http://keycloak:8080/realms/northwind/.well-known/openid-configuration' `
        --client-id axyntro --secret-ref 'env:TENANT_NORTHWIND_OIDC_CLIENT_SECRET' --display-name 'Northwind SSO' `
        --acr-values mfa --mfa-acr mfa --admin-group northwind-admins | Out-Null
}
else {
    & docker compose exec -T api python -m app.tenancy identity reset northwind | Out-Null
}
Write-Host 'Tenants:  http://meridian.localhost:3000 (same as localhost) and http://northwind.localhost:3000'
if ($Sso) {
    Write-Host '          Each signs in through its own Keycloak realm (testing\keycloak\README.md).'
}

if ($Seed -or $SeedProcessTwin) {
    Require-Command 'python'
    if ($Seed) {
        & python (Join-Path $PSScriptRoot 'backend\scripts\demo_seed\seed_demo.py') --base-url 'http://localhost:8000'
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Seeding failed (exit code $LASTEXITCODE). Fix the cause, then re-run with -Demo -ResetDemo -Seed."
            exit 1
        }
    }
    if ($SeedProcessTwin) {
        & python (Join-Path $PSScriptRoot 'backend\scripts\demo_seed\seed_process_twin.py') --base-url 'http://localhost:8000'
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Seeding failed (exit code $LASTEXITCODE). Fix the cause, then re-run with -Demo -ResetDemo -SeedProcessTwin."
            exit 1
        }
    }
}

if ($OpenBrowser) {
    Start-Process 'http://localhost:3000'
}
