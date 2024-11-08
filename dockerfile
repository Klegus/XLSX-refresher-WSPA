FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY plans.json .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY .bandit.yml .
COPY .codecov.yml .
COPY mypy.ini .
COPY .safety-policy.yml . 


CMD ["python", "main.py"]
