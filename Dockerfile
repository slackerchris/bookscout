FROM python:3.11-slim

WORKDIR /app

# Install system deps (psycopg2 needs libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY confidence.py .
COPY config.py .
COPY main.py .
COPY cli.py .
COPY VERSION .
COPY alembic.ini .
COPY core/ core/
COPY api/ api/
COPY workers/ workers/
COPY db/ db/
COPY scripts/ scripts/

# Writable data directory
RUN mkdir -p /data
VOLUME /data

ENV PYTHONUNBUFFERED=1
ENV BOOKSCOUT_CONFIG=/data/config.yaml

# API port
EXPOSE 8765

# Default: run the FastAPI service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
