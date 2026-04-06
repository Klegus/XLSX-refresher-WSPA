#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose.test.yml"
BASE_URL="http://localhost:5006"

require_docker() {
  if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is not running. Start Docker Desktop and retry."
    exit 1
  fi
}

up() {
  require_docker
  docker compose -f "${COMPOSE_FILE}" up -d --build
  echo "Stack started."
}

down() {
  require_docker
  docker compose -f "${COMPOSE_FILE}" down
  echo "Stack stopped."
}

logs() {
  require_docker
  docker compose -f "${COMPOSE_FILE}" logs -f
}

smoke() {
  require_docker
  echo "Checking ${BASE_URL}/api/status"
  curl -fsS "${BASE_URL}/api/status" | sed -n '1,120p'
  echo
  echo "Checking ${BASE_URL}/api/config"
  curl -fsS "${BASE_URL}/api/config" | sed -n '1,120p'
  echo
  echo "Smoke test OK."
}

status() {
  require_docker
  docker compose -f "${COMPOSE_FILE}" ps
}

cmd="${1:-}"
case "${cmd}" in
  up) up ;;
  down) down ;;
  logs) logs ;;
  smoke) smoke ;;
  status) status ;;
  *)
    echo "Usage: $0 {up|down|logs|status|smoke}"
    exit 1
    ;;
esac
