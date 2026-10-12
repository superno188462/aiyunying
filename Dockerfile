# ==============================================================================
# Spider_XHS Multi-Stage Dockerfile
# ==============================================================================
# Stage 1: Build the frontend (React + Vite)
# Stage 2: Build the Python application with pre-built frontend assets
#
# Usage:
#   docker compose build app
#   docker compose up -d --wait app
# ==============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Frontend build
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app/frontend

# Install dependencies first (layer caching)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy source and build
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python application
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS signature-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies required by some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Node.js is required by PyExecJS; both stages use Debian Bookworm.
COPY --from=signature-deps /usr/local/bin/node /usr/local/bin/node

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Copy pre-built frontend from Stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Install root-level Node.js deps (signature JS execution)
COPY --from=signature-deps /app/node_modules ./node_modules

# Create data directory for SQLite and storage
RUN mkdir -p /app/data /app/backend/app/storage/media /app/backend/app/storage/exports /app/backend/app/storage/content_cards
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && chown -R app:app /app

# Set environment defaults
ENV PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    FRONTEND_SERVE_STATIC=true \
    FRONTEND_BUILD_DIR=/app/frontend/dist \
    TZ=Asia/Shanghai \
    CONFIG_FILE=/app/config/default.yaml

USER app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "240"]
