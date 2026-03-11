FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc g++ libpq-dev curl git \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create log directory
RUN mkdir -p /app/logs

# Non-root user
RUN useradd -m -u 1000 trader && chown -R trader:trader /app
USER trader

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start in API-only mode by default
# Engine can be started via POST /api/engine/start from the dashboard
CMD ["python", "main.py", "--api-only", "--log-level", "INFO"]
