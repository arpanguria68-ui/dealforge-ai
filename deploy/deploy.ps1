# DealForge AI — Windows Deployment Orchestrator
# Automates the build and deployment on Windows systems

$ComposeFile = "docker-compose.prod.yml"
$EnvFile = ".env.docker"

Write-Host "Starting DealForge AI Deployment..." -ForegroundColor Cyan

# 1. Pre-flight Checks
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Error: Docker is not installed."
    exit
}

if (!(Get-Command "docker-compose" -ErrorAction SilentlyContinue) -and !(Get-Command "docker" -ErrorAction SilentlyContinue)) {
     Write-Error "Error: Docker or Docker Compose is not available."
     exit
}

if (!(Test-Path $EnvFile)) {
    Write-Warning "Warning: $EnvFile not found."
    Write-Host "Please create it with your API keys before deploying."
    exit
}

# 2. Network Staging
Write-Host "Checking Docker network..." -ForegroundColor Gray
$Networks = docker network ls --format "{{.Name}}"
if ($Networks -notcontains "dealforge-net") {
    docker network create dealforge-net
}

# 3. Pull/Build
Write-Host "Building services (this may take a few minutes)..." -ForegroundColor Gray
docker compose -f $ComposeFile --env-file $EnvFile build --pull

# 4. Deploy
Write-Host "Deploying stack..." -ForegroundColor Green
docker compose -f $ComposeFile --env-file $EnvFile up -d

# 5. Health Check
Write-Host "Waiting for services to stabilize..." -ForegroundColor Gray
Start-Sleep -Seconds 15
docker compose -f $ComposeFile ps

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SUCCESS: DealForge AI is deployed." -ForegroundColor Green
Write-Host "  Backend: http://localhost:8005"
Write-Host "  Frontend: http://localhost:3000"
Write-Host "============================================================" -ForegroundColor Cyan
