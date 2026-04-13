# Multi-stage build
#   builder  → installs dependencies to an isolated prefix (/deps)
#   runtime  → copies /deps into /usr/local (world-readable), then drops to non-root
#
# Key fix: previously packages were installed into /root/.local and the PATH
# was set to /root/.local/bin. After USER appuser, Debian slim's /root is
# mode 700 so uvicorn and site-packages become inaccessible. Installing
# into a neutral prefix (/deps → /usr/local) avoids this entirely.

# ── Stage 1: dependency installation ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .

# Install to /deps so we can copy atomically into the runtime image.
# --no-cache-dir keeps the layer small; --prefix isolates from system Python.
RUN pip install --no-cache-dir --prefix=/deps -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as non-root user.
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages into /usr/local — world-readable, on Python's
# default sys.path, accessible to all users including appuser.
COPY --from=builder /deps /usr/local

# Copy application source only (no tests, docs, or .env files).
COPY app/ ./app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BACKEND=mock \
    LOG_FORMAT=json \
    LOG_LEVEL=INFO

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
