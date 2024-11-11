# Build stage
FROM python:3.12-alpine as builder

WORKDIR /app

# Instalacja tylko niezbędnych pakietów do budowania
RUN apk add --no-cache gcc musl-dev

# Kopiowanie i instalacja zależności
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.12-alpine

WORKDIR /app

# Kopiowanie zainstalowanych pakietów z builder stage
COPY --from=builder /root/.local /root/.local

# Ustawienie PATH dla zainstalowanych pakietów
ENV PATH=/root/.local/bin:$PATH

# Kopiowanie tylko niezbędnych plików aplikacji
COPY plans.json .
COPY *.py .
COPY .bandit.yml .
COPY .codecov.yml .
COPY mypy.ini .
COPY .safety-policy.yml .

# Ustawienie użytkownika nieprivilegowanego
RUN adduser -D appuser && \
    chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]
