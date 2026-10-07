#!/bin/bash

# DealForge AI — Deployment Orchestrator
# Automates the build and deployment of the DealForge stack

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.docker"

echo "🚀 Starting DealForge AI Deployment..."

# 1. Pre-flight Checks
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "❌ Error: Docker Compose (v2) is not installed."
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️ Warning: $ENV_FILE not found."
    echo "Run ./deploy/setup_env.sh first to generate it."
    exit 1
fi

# 2. Network Staging
echo "📡 Checking Docker network..."
docker network inspect dealforge-net &>/dev/null || docker network create dealforge-net

# 3. Pull/Build
echo "🛠️ Building services..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --pull

# 4. Deploy
echo "🚢 Deploying stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

# 5. Health Check
echo "🔍 Waiting for services to stabilize..."
sleep 15
docker compose -f "$COMPOSE_FILE" ps

echo "════════════════════════════════════════════════════════════"
echo "  SUCCESS: DealForge AI is deployed."
echo "  Backend: http://localhost:8005"
echo "  Frontend: http://localhost:3000"
echo "════════════════════════════════════════════════════════════"
