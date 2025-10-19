#!/bin/bash

# Skrypt do budowania i pushowania obrazu Docker dla Python Flask Backend
# Użycie: ./docker-build-push.sh <tag>
# Przykład: ./docker-build-push.sh v1.0.0

set -e  # Zatrzymaj przy błędzie

# Kolory dla lepszej czytelności
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Konfiguracja
DOCKER_REPO="klegus/backend-plan"
DOCKERFILE="Dockerfile"

# Sprawdź czy podano tag
if [ -z "$1" ]; then
    echo -e "${RED}❌ Błąd: Nie podano tagu!${NC}"
    echo -e "${YELLOW}Użycie: $0 <tag>${NC}"
    echo -e "${YELLOW}Przykład: $0 v1.0.0${NC}"
    exit 1
fi

TAG=$1
FULL_IMAGE_NAME="${DOCKER_REPO}:${TAG}"
LATEST_IMAGE_NAME="${DOCKER_REPO}:latest"

echo -e "${GREEN}🚀 Rozpoczynam proces budowania i publikacji obrazu Docker${NC}"
echo -e "📦 Repozytorium: ${YELLOW}${DOCKER_REPO}${NC}"
echo -e "🏷️  Tag: ${YELLOW}${TAG}${NC}"
echo ""

# Sprawdź czy użytkownik jest zalogowany do Docker Hub
echo -e "${GREEN}🔐 Sprawdzam logowanie do Docker Hub...${NC}"
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo -e "${YELLOW}⚠️  Nie jesteś zalogowany do Docker Hub${NC}"
    echo -e "${YELLOW}Zaloguj się używając: docker login${NC}"
    docker login
fi

# Sprawdź czy Dockerfile istnieje
if [ ! -f "$DOCKERFILE" ]; then
    echo -e "${RED}❌ Błąd: Nie znaleziono pliku Dockerfile!${NC}"
    exit 1
fi

# Sprawdź czy requirements.txt istnieje
if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}❌ Błąd: Nie znaleziono pliku requirements.txt!${NC}"
    exit 1
fi

# Opcjonalnie: uruchom testy przed budowaniem
echo -e "${GREEN}🧪 Czy chcesz uruchomić testy przed budowaniem? (y/N): ${NC}"
read -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}🧪 Uruchamiam testy...${NC}"

    # Sprawdź kod za pomocą flake8
    if command -v flake8 &> /dev/null; then
        echo -e "${BLUE}📝 Sprawdzam styl kodu (flake8)...${NC}"
        flake8 *.py --max-line-length=100 --exclude=test_*.py --ignore=F401,F403 || true
    fi

    # Sprawdź typy za pomocą mypy
    if command -v mypy &> /dev/null && [ -f "mypy.ini" ]; then
        echo -e "${BLUE}🔍 Sprawdzam typy (mypy)...${NC}"
        mypy *.py --config-file mypy.ini \
            --allow-untyped-defs \
            --disable-error-code=no-untyped-def \
            --disable-error-code=no-untyped-call || true
    fi

    # Uruchom pytest jeśli istnieją testy
    if command -v pytest &> /dev/null && [ -d "tests" -o -f "test_*.py" ]; then
        echo -e "${BLUE}🧪 Uruchamiam testy jednostkowe (pytest)...${NC}"
        pytest --tb=short || true
    fi
fi

# Budowanie obrazu Docker
echo -e "${GREEN}🔨 Buduję obraz Docker...${NC}"
docker build -t ${FULL_IMAGE_NAME} .
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Błąd podczas budowania obrazu Docker${NC}"
    exit 1
fi

# Tagowanie jako latest
echo -e "${GREEN}🏷️  Taguję obraz jako 'latest'...${NC}"
docker tag ${FULL_IMAGE_NAME} ${LATEST_IMAGE_NAME}

# Test obrazu - sprawdź czy kontener się uruchamia
echo -e "${GREEN}🧪 Testuję obraz lokalnie...${NC}"
CONTAINER_NAME="test-backend-${TAG}"
docker run -d --name ${CONTAINER_NAME} -p 5005:80 ${FULL_IMAGE_NAME}
sleep 5

# Sprawdź czy kontener działa
if docker ps | grep -q ${CONTAINER_NAME}; then
    echo -e "${GREEN}✅ Kontener testowy działa poprawnie${NC}"

    # Sprawdź endpoint /api/status
    echo -e "${BLUE}🔍 Sprawdzam endpoint /api/status...${NC}"
    curl -s http://localhost:5005/api/status > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ API odpowiada poprawnie${NC}"
    else
        echo -e "${YELLOW}⚠️  API nie odpowiada na /api/status${NC}"
    fi

    # Zatrzymaj i usuń kontener testowy
    docker stop ${CONTAINER_NAME} > /dev/null
    docker rm ${CONTAINER_NAME} > /dev/null
else
    echo -e "${YELLOW}⚠️  Kontener testowy nie uruchomił się poprawnie${NC}"
    docker rm ${CONTAINER_NAME} > /dev/null 2>&1
fi

# Pushowanie obrazu z konkretnym tagiem
echo -e "${GREEN}📤 Wysyłam obraz z tagiem ${TAG}...${NC}"
docker push ${FULL_IMAGE_NAME}
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Błąd podczas wysyłania obrazu${NC}"
    exit 1
fi

# Pushowanie obrazu jako latest
echo -e "${GREEN}📤 Wysyłam obraz z tagiem 'latest'...${NC}"
docker push ${LATEST_IMAGE_NAME}
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Błąd podczas wysyłania obrazu 'latest'${NC}"
    exit 1
fi

# Pokaż informacje o zbudowanym obrazie
echo ""
echo -e "${GREEN}✅ Sukces! Obraz został zbudowany i wysłany do Docker Hub${NC}"
echo -e "📦 Obraz: ${YELLOW}${FULL_IMAGE_NAME}${NC}"
echo -e "📦 Latest: ${YELLOW}${LATEST_IMAGE_NAME}${NC}"
echo ""
echo -e "${GREEN}Aby pobrać obraz, użyj:${NC}"
echo -e "  docker pull ${FULL_IMAGE_NAME}"
echo ""
echo -e "${GREEN}Aby uruchomić kontener:${NC}"
echo -e "  docker run -p 5005:80 --env-file .env ${FULL_IMAGE_NAME}"
echo ""

# Opcjonalnie: wyczyść lokalne obrazy
read -p "Czy chcesz usunąć lokalne obrazy Docker? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}🧹 Usuwam lokalne obrazy...${NC}"
    docker rmi ${FULL_IMAGE_NAME} ${LATEST_IMAGE_NAME}
    echo -e "${GREEN}✅ Lokalne obrazy zostały usunięte${NC}"
fi

echo -e "${GREEN}🎉 Proces zakończony pomyślnie!${NC}"