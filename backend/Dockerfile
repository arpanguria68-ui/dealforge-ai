# DealForge AI Backend Dockerfile — DealForge 2.0
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    fonts-liberation \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (Docker layer caching)
COPY requirements.txt constraints.txt ./

# Install Python dependencies + aiosqlite for SQLite async support
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt aiosqlite

# Copy the full application
COPY app/ ./app/

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV RUNNING_IN_DOCKER=1
ENV DATABASE_URL=sqlite+aiosqlite:///./data/dealforge.db
ENV DATA_DIR=data
ENV REPORTS_DIR=reports
ENV MEMORY_STALENESS_DAYS=30
ENV DEAL_ISOLATION_MODE=1
ENV SEARCH_CACHE_TTL=86400
ENV MAX_CLARIFICATION_DEPTH=3
# Expose port (Cloud Run uses $PORT, but we'll document 8000 as default)
EXPOSE 8000

# Run the application (use $PORT for Cloud Run compatibility, default to 8000)
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
