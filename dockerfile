# Build stage
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y gcc

# Copy requirements and install dependencies
COPY requirements.txt .
# Upgrade pip first to ensure we have the latest version
RUN pip install --upgrade pip
# Install with retries and increased timeout
# Split into groups to avoid timeouts on large packages
RUN pip install --no-cache-dir --timeout=120 --retries=5 \
    Flask==3.0.3 \
    pymongo==4.10.0 \
    python-dotenv==1.0.1 \
    requests==2.32.3 \
    pytz==2024.2 \
    colorama==0.4.6
RUN pip install --no-cache-dir --timeout=120 --retries=5 \
    pandas==2.2.3 \
    numpy==2.0.2
RUN pip install --no-cache-dir --timeout=120 --retries=5 \
    beautifulsoup4==4.12.3 \
    openpyxl==3.1.5 \
    lxml==5.3.0
RUN pip install --no-cache-dir --timeout=120 --retries=5 \
    selenium==4.25.0
RUN pip install --no-cache-dir --timeout=120 --retries=5 \
    sentry-sdk==2.18.0 \
    fastapi==0.115.0 \
    pywebpush==2.0.3 \
    discord.py==2.4.0 \
    boto3==1.35.77 \
    watchtower==2.0.1

# Copy application files
COPY *.py ./
COPY *.json ./
COPY routes/ routes/
COPY templates/ templates/

# Final stage
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages and application files from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /app /app

# Create non-root user
RUN addgroup --system app && adduser --system --group app \
    && chown -R app:app /app

# Switch to non-root user
USER app

# Expose port
EXPOSE 80

# Run the application
CMD ["python", "main.py"]