#!/bin/bash
# .github/workflows/templates/deploy-scripts/deploy.sh

set -e
set -o pipefail

# Funkcja do logowania
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Funkcja do walidacji zmiennych środowiskowych
check_required_vars() {
    local missing_vars=()
    for var in "$@"; do
        if [ -z "${!var}" ]; then
            missing_vars+=("$var")
        fi
    done
    
    if [ ${#missing_vars[@]} -ne 0 ]; then
        log "ERROR: Missing required environment variables: ${missing_vars[*]}"
        exit 1
    fi
}

# Sprawdzenie wymaganych zmiennych
check_required_vars "REGISTRY_URL" "REGISTRY_USERNAME" "REGISTRY_PASSWORD" "BRANCH_NAME" "IMAGE_NAME"

# Ustawienie konfiguracji na podstawie brancha
case "$BRANCH_NAME" in
    "dev")
        PORT="8080"
        CONTAINER_SUFFIX="dev"
        ;;
    "staging")
        PORT="8081"
        CONTAINER_SUFFIX="staging"
        ;;
    "secondyear")
        PORT="8081"
        CONTAINER_SUFFIX="secondyear"
        ;;
    *)
        log "ERROR: Unsupported branch: $BRANCH_NAME"
        exit 1
        ;;
esac

# Ustawienie pełnej nazwy kontenera
CONTAINER_NAME="${IMAGE_NAME}-${CONTAINER_SUFFIX}"

log "Starting deployment for branch: $BRANCH_NAME"
log "Container: $CONTAINER_NAME"
log "Port: $PORT"

# Logowanie do rejestru
log "Logging into container registry"
docker login ${REGISTRY_URL} -u ${REGISTRY_USERNAME} -p ${REGISTRY_PASSWORD}

# Pobieranie najnowszego obrazu
log "Pulling latest image for $BRANCH_NAME"
docker pull ${REGISTRY_URL}/${IMAGE_NAME}:${BRANCH_NAME}

# Zatrzymanie i usunięcie istniejącego kontenera
log "Stopping existing container (if exists)"
docker stop ${CONTAINER_NAME} || true
docker rm ${CONTAINER_NAME} || true

# Uruchomienie nowego kontenera z odpowiednią konfiguracją
log "Starting new container"
docker run -d \
    --name ${CONTAINER_NAME} \
    --restart unless-stopped \
    -p ${PORT}:80 \
    -e ENVIRONMENT="${BRANCH_NAME}" \
    -e EMAIL="${EMAIL}" \
    -e SENTRY_DSN="${SENTRY_DSN}" \
    -e PASSWORD="${PASSWORD}" \
    -e MONGO_DB="${MONGO_DB}" \
    -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY}" \
    -e SELECTED_MODEL="${SELECTED_MODEL}" \
    -e DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL}" \
    -e MONGO_URI="${MONGO_URI}" \
    -e WERKZEUG_RUN_MAIN="${WERKZEUG_RUN_MAIN}" \
    -e ENABLE_COMPARER="${ENABLE_COMPARER}" \
    ${REGISTRY_URL}/${IMAGE_NAME}:${BRANCH_NAME}

# Sprawdzenie statusu kontenera
if docker ps | grep -q ${CONTAINER_NAME}; then
    log "Container is running successfully"
    log "Application is available on port: ${PORT}"
else
    log "ERROR: Container failed to start"
    docker logs ${CONTAINER_NAME}
    exit 1
fi