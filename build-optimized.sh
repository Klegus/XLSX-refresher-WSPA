#!/bin/bash

# Skrypt do budowania zoptymalizowanego obrazu Docker
# Użycie: ./build-optimized.sh [tag]

set -e

# Kolory
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Domyślny tag jeśli nie podano
TAG=${1:-latest}
IMAGE_NAME="klegus/backend-plan:${TAG}"

echo -e "${GREEN}🚀 Budowanie zoptymalizowanego obrazu Docker${NC}"
echo -e "📦 Obraz: ${YELLOW}${IMAGE_NAME}${NC}"
echo ""

# Buduj używając Dockerfile.optimized
echo -e "${GREEN}🔨 Budowanie obrazu...${NC}"
docker build -f Dockerfile.optimized -t ${IMAGE_NAME} .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Obraz zbudowany pomyślnie!${NC}"
    echo ""
    echo -e "${GREEN}Aby uruchomić kontener:${NC}"
    echo -e "  docker run -p 5005:80 --env-file .env ${IMAGE_NAME}"
    echo ""
    echo -e "${GREEN}Aby wypchnąć do Docker Hub:${NC}"
    echo -e "  docker push ${IMAGE_NAME}"
else
    echo -e "${RED}❌ Błąd podczas budowania obrazu${NC}"
    exit 1
fi