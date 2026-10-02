# Build stage
FROM python:3.14-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
# Upgrade pip first to ensure we have the latest version
RUN pip install --upgrade pip
# Versions are pinned in requirements.txt (single source of truth, audited with pip-audit)
RUN pip install --no-cache-dir --timeout=120 --retries=5 -r requirements.txt

# Copy application files
COPY *.py ./
COPY routes/ routes/
COPY templates/ templates/

# Test stage: runs the whole suite on every image build (anonymised real plans,
# no network, no database). A failing test stops the build.
FROM builder AS test
COPY tests/ tests/
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt \
    && python -m pytest -q -p no:cacheprovider tests \
    && touch /tmp/tests-passed

# Final stage
FROM python:3.14-slim

WORKDIR /app

# Copy installed packages and application files from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /app /app
# Depending on the test stage forces it to run - the image only exists if tests passed
COPY --from=test /tmp/tests-passed /tmp/tests-passed

# Local time for schedule logic (night pause, auto-scan window) and timestamps
ENV TZ=Europe/Warsaw

# Create non-root user
RUN addgroup --system app && adduser --system --group app \
    && chown -R app:app /app

# Switch to non-root user
USER app

# Expose port (PORT, default 5005)
EXPOSE 5005

# Run the application
CMD ["python", "main.py"]