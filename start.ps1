[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$OpenBrowser
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

Write-Host 'Starting Agentic Solution Generator...' -ForegroundColor Cyan

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
Write-Host 'Frontend: http://localhost:3000'
Write-Host 'API:      http://localhost:8000'
Write-Host ''
Write-Host 'To stop it later, run: docker compose down'

if ($OpenBrowser) {
    Start-Process 'http://localhost:3000'
}
