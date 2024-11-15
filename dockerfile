# Build stage
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y gcc

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

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